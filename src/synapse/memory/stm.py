"""Short-term memory: durable agent runs + append-only checkpoints.

STM is execution state for one ask — not long-term semantic memory (Chunk 9).
Postgres is the source of truth so a crash mid-run leaves an inspectable trail.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from synapse.platform.schema import AgentCheckpointRow, AgentRunRow


def _now() -> datetime:
    return datetime.now(UTC)


def _run_id() -> str:
    return f"run_{uuid4().hex[:16]}"


def _ckpt_id() -> str:
    return f"ckpt_{uuid4().hex[:16]}"


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
