"""Short-term memory: durable agent runs + append-only checkpoints.

STM is execution state for one ask — not long-term semantic memory (Chunk 9).
Postgres is the source of truth so a crash mid-run leaves an inspectable trail.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from synapse.platform.schema import AgentCheckpointRow, AgentRunRow

_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")


class BadIdempotencyKey(ValueError):
    pass


@dataclass(frozen=True)
class RunClaim:
    """Result of claiming an ask. `replay` means do not run the agent again."""

    action: Literal["execute", "replay", "in_progress", "mismatch"]
    run_id: str
    response: dict[str, Any] | None = None


def _now() -> datetime:
    return datetime.now(UTC)


def _run_id() -> str:
    return f"run_{uuid4().hex[:16]}"


def _ckpt_id() -> str:
    return f"ckpt_{uuid4().hex[:16]}"


def _replay_body(run: AgentRunRow) -> dict[str, Any]:
    if isinstance(run.response_body, dict):
        body = dict(run.response_body)
        body["replayed"] = True
        return body
    return {
        "run_id": run.id,
        "answer": run.answer or "",
        "citations": list(run.citations or []),
        "gaps": list(run.gaps or []),
        "conflicts": list(run.conflicts or []),
        "plan": list(run.plan or []),
        "checklist": dict(run.checklist or {}),
        "tool_trail": list(run.tool_trail or []),
        "usage": dict(run.usage or {}),
        "replayed": True,
        "replay": "partial",
    }


class ShortTermMemory:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def start_run(
        self,
        *,
        tenant_id: str,
        user_id: str,
        project_key: str,
        question: str,
    ) -> str:
        run_id = _run_id()
        now = _now()
        async with self._sessions() as session:
            session.add(
                AgentRunRow(
                    id=run_id,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    project_key=project_key,
                    question=question,
                    status="running",
                    phase="started",
                    plan=[],
                    checklist={},
                    tool_trail=[],
                    citations=[],
                    gaps=[],
                    conflicts=[],
                    usage={},
                    idempotency_key=None,
                    response_body=None,
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.commit()
        await self.checkpoint(
            run_id,
            tenant_id=tenant_id,
            phase="started",
            payload={"question": question, "project_key": project_key},
        )
        return run_id

    async def checkpoint(
        self,
        run_id: str,
        *,
        tenant_id: str,
        phase: str,
        payload: dict[str, Any],
    ) -> int:
        async with self._sessions() as session:
            run = await self._get(session, tenant_id, run_id)
            if run is None:
                raise LookupError("run not found")
            step = await self._next_step(session, run_id)
            session.add(
                AgentCheckpointRow(
                    id=_ckpt_id(),
                    run_id=run_id,
                    tenant_id=tenant_id,
                    step_index=step,
                    phase=phase,
                    payload=payload,
                    created_at=_now(),
                )
            )
            run.phase = phase
            run.updated_at = _now()
            if "plan" in payload:
                run.plan = payload["plan"]
            if "checklist" in payload:
                run.checklist = payload["checklist"]
            if "tool_trail" in payload:
                run.tool_trail = payload["tool_trail"]
            await session.commit()
            return step

    async def complete(
        self,
        run_id: str,
        *,
        tenant_id: str,
        answer: str,
        citations: list[dict[str, Any]],
        gaps: list[str],
        conflicts: list[dict[str, Any]],
        plan: list[str],
        checklist: dict[str, str],
        tool_trail: list[dict[str, Any]],
        usage: dict[str, Any],
    ) -> None:
        async with self._sessions() as session:
            run = await self._get(session, tenant_id, run_id)
            if run is None:
                raise LookupError("run not found")
            now = _now()
            run.status = "completed"
            run.phase = "done"
            run.answer = answer
            run.citations = citations
            run.gaps = gaps
            run.conflicts = conflicts
            run.plan = plan
            run.checklist = checklist
            run.tool_trail = tool_trail
            run.usage = usage
            run.updated_at = now
            run.finished_at = now
            step = await self._next_step(session, run_id)
            session.add(
                AgentCheckpointRow(
                    id=_ckpt_id(),
                    run_id=run_id,
                    tenant_id=tenant_id,
                    step_index=step,
                    phase="done",
                    payload={"status": "completed", "usage": usage},
                    created_at=now,
                )
            )
            await session.commit()

    async def fail(self, run_id: str, *, tenant_id: str, error: str) -> None:
        async with self._sessions() as session:
            run = await self._get(session, tenant_id, run_id)
            if run is None:
                return
            now = _now()
            run.status = "failed"
            run.phase = "failed"
            run.error = error[:4000]
            run.updated_at = now
            run.finished_at = now
            step = await self._next_step(session, run_id)
            session.add(
                AgentCheckpointRow(
                    id=_ckpt_id(),
                    run_id=run_id,
                    tenant_id=tenant_id,
                    step_index=step,
                    phase="failed",
                    payload={"error": error[:2000]},
                    created_at=now,
                )
            )
            await session.commit()

    async def claim_run(
        self,
        *,
        tenant_id: str,
        user_id: str,
        project_key: str,
        question: str,
        idempotency_key: str | None,
    ) -> RunClaim:
        """Start a run, or return the previous result for the same Idempotency-Key.

        Completed asks are replayed. A key still running returns in_progress so a
        client retry does not start a second agent. A failed ask may be reclaimed
        by one caller (compare-and-set on status) and executed again.
        """
        if idempotency_key is None:
            run_id = await self.start_run(
                tenant_id=tenant_id,
                user_id=user_id,
                project_key=project_key,
                question=question,
            )
            return RunClaim("execute", run_id)

        key = idempotency_key.strip()
        if not _IDEMPOTENCY_KEY.fullmatch(key):
            raise BadIdempotencyKey("idempotency key must be 8–128 chars of [A-Za-z0-9._:-]")

        inserted_id: str | None = None
        failed_id: str | None = None
        async with self._sessions() as session:
            existing = await self._by_key(session, tenant_id, user_id, key)
            if existing is None:
                inserted_id = _run_id()
                now = _now()
                session.add(
                    AgentRunRow(
                        id=inserted_id,
                        tenant_id=tenant_id,
                        user_id=user_id,
                        project_key=project_key,
                        question=question,
                        status="running",
                        phase="started",
                        plan=[],
                        checklist={},
                        tool_trail=[],
                        citations=[],
                        gaps=[],
                        conflicts=[],
                        usage={},
                        idempotency_key=key,
                        response_body=None,
                        created_at=now,
                        updated_at=now,
                    )
                )
                try:
                    await session.commit()
                except IntegrityError:
                    await session.rollback()
                    inserted_id = None
                    existing = await self._by_key(session, tenant_id, user_id, key)
                    if existing is None:
                        raise
            if inserted_id is not None:
                pass
            else:
                assert existing is not None
                mismatch = self._key_mismatch(existing, project_key, question)
                if mismatch is not None:
                    return mismatch
                if existing.status == "completed":
                    return RunClaim("replay", existing.id, _replay_body(existing))
                if existing.status == "running":
                    return RunClaim("in_progress", existing.id)
                inserted_id = None
                failed_id = existing.id

        if inserted_id is not None:
            await self.checkpoint(
                inserted_id,
                tenant_id=tenant_id,
                phase="started",
                payload={
                    "question": question,
                    "project_key": project_key,
                    "idempotency_key": key,
                },
            )
            return RunClaim("execute", inserted_id)

        if failed_id is None:
            raise RuntimeError("idempotency claim lost the run id")
        won = await self._reclaim_failed(tenant_id, failed_id)
        if not won:
            current = await self.get_run(tenant_id=tenant_id, run_id=failed_id)
            if current is not None and current.status == "completed":
                return RunClaim("replay", current.id, _replay_body(current))
            return RunClaim("in_progress", failed_id)
        await self.checkpoint(
            failed_id,
            tenant_id=tenant_id,
            phase="retry",
            payload={"idempotency_key": key},
        )
        return RunClaim("execute", failed_id)

    async def save_response(
        self, run_id: str, *, tenant_id: str, response: dict[str, Any]
    ) -> None:
        async with self._sessions() as session:
            run = await self._get(session, tenant_id, run_id)
            if run is None:
                return
            run.response_body = response
            run.updated_at = _now()
            await session.commit()

    async def fail_orphaned_runs(self) -> int:
        """Mark every running ask failed. Safe only for a single API process at startup.

        A restart means no worker still owns those rows. With multiple replicas this
        would kill live asks — do not call it from each worker.
        """
        async with self._sessions() as session:
            rows = list(
                (
                    await session.execute(
                        select(AgentRunRow).where(AgentRunRow.status == "running")
                    )
                ).scalars()
            )
            now = _now()
            for run in rows:
                run.status = "failed"
                run.phase = "failed"
                run.error = "orphaned_on_restart"
                run.updated_at = now
                run.finished_at = now
                step = await self._next_step(session, run.id)
                session.add(
                    AgentCheckpointRow(
                        id=_ckpt_id(),
                        run_id=run.id,
                        tenant_id=run.tenant_id,
                        step_index=step,
                        phase="failed",
                        payload={"error": "orphaned_on_restart"},
                        created_at=now,
                    )
                )
            await session.commit()
            return len(rows)

    async def _reclaim_failed(self, tenant_id: str, run_id: str) -> bool:
        """Return True only if this caller moved failed → running."""
        now = _now()
        async with self._sessions() as session:
            result = await session.execute(
                update(AgentRunRow)
                .where(
                    AgentRunRow.id == run_id,
                    AgentRunRow.tenant_id == tenant_id,
                    AgentRunRow.status == "failed",
                )
                .values(
                    status="running",
                    phase="started",
                    error=None,
                    finished_at=None,
                    updated_at=now,
                )
            )
            await session.commit()
            return int(result.rowcount or 0) == 1

    async def _by_key(
        self, session: AsyncSession, tenant_id: str, user_id: str, key: str
    ) -> AgentRunRow | None:
        result = await session.execute(
            select(AgentRunRow).where(
                AgentRunRow.tenant_id == tenant_id,
                AgentRunRow.user_id == user_id,
                AgentRunRow.idempotency_key == key,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    def _key_mismatch(existing: AgentRunRow, project_key: str, question: str) -> RunClaim | None:
        if existing.project_key != project_key or existing.question != question:
            return RunClaim("mismatch", existing.id)
        return None

    async def get_run(self, *, tenant_id: str, run_id: str) -> AgentRunRow | None:
        async with self._sessions() as session:
            return await self._get(session, tenant_id, run_id)

    async def list_checkpoints(
        self, *, tenant_id: str, run_id: str
    ) -> list[AgentCheckpointRow]:
        async with self._sessions() as session:
            run = await self._get(session, tenant_id, run_id)
            if run is None:
                return []
            result = await session.execute(
                select(AgentCheckpointRow)
                .where(
                    AgentCheckpointRow.run_id == run_id,
                    AgentCheckpointRow.tenant_id == tenant_id,
                )
                .order_by(AgentCheckpointRow.step_index)
            )
            return list(result.scalars())

    async def _get(
        self, session: AsyncSession, tenant_id: str, run_id: str
    ) -> AgentRunRow | None:
        result = await session.execute(
            select(AgentRunRow).where(
                AgentRunRow.id == run_id, AgentRunRow.tenant_id == tenant_id
            )
        )
        return result.scalar_one_or_none()

    async def _next_step(self, session: AsyncSession, run_id: str) -> int:
        result = await session.execute(
            select(AgentCheckpointRow.step_index)
            .where(AgentCheckpointRow.run_id == run_id)
            .order_by(AgentCheckpointRow.step_index.desc())
            .limit(1)
        )
        last = result.scalar_one_or_none()
        return 0 if last is None else int(last) + 1
