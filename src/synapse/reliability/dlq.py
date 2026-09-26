"""Dead-letter lite for sync asks — failed STM runs are inspectable poison.

True SQS/DLQ comes with async workers. Until then, failed
`agent_runs` *are* the dead letter: list, inspect checkpoints, acknowledge.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from synapse.platform.schema import AgentRunRow


class DeadLetterLite:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def list_failed(
        self, *, tenant_id: str, limit: int = 50, include_acked: bool = False
    ) -> list[dict[str, Any]]:
        async with self._sessions() as session:
            stmt = (
                select(AgentRunRow)
                .where(
                    AgentRunRow.tenant_id == tenant_id,
                    AgentRunRow.status == "failed",
                )
                .order_by(desc(AgentRunRow.finished_at).nulls_last())
                .limit(limit)
            )
            rows = list((await session.execute(stmt)).scalars())
        out: list[dict[str, Any]] = []
        for r in rows:
            usage = r.usage or {}
            acked = bool(usage.get("dlq_acked"))
            if acked and not include_acked:
                continue
            out.append(
                {
                    "id": r.id,
                    "project_key": r.project_key,
                    "question": r.question,
                    "error": r.error,
                    "phase": r.phase,
                    "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                    "dlq_acked": acked,
                }
            )
        return out

    async def acknowledge(self, *, tenant_id: str, run_id: str) -> bool:
        """Mark a failed run as reviewed — stays failed, leaves the active DLQ list."""
        async with self._sessions() as session:
            run = (
                await session.execute(
                    select(AgentRunRow).where(
                        AgentRunRow.tenant_id == tenant_id, AgentRunRow.id == run_id
                    )
                )
            ).scalar_one_or_none()
            if run is None or run.status != "failed":
                return False
            usage = dict(run.usage or {})
            usage["dlq_acked"] = True
            usage["dlq_acked_at"] = datetime.now(UTC).isoformat()
            run.usage = usage
            run.updated_at = datetime.now(UTC)
            await session.commit()
            return True
