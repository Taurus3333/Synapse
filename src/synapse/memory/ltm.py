"""Long-term memory: durable tenant/project notes. Never overrides live Postgres status."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from synapse.platform.schema import MemoryEntryRow

KINDS = frozenset({"fact", "summary", "preference"})
DEFAULT_TTL_DAYS = 90


def _now() -> datetime:
    return datetime.now(UTC)


def _mem_id() -> str:
    return f"mem_{uuid4().hex[:16]}"


class LongTermMemory:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def write(
        self,
        *,
        tenant_id: str,
        content: str,
        kind: str = "fact",
        user_id: str | None = None,
        project_key: str | None = None,
        source_run_id: str | None = None,
        confidence: float = 0.6,
        ttl_days: int = DEFAULT_TTL_DAYS,
        supersedes: str | None = None,
    ) -> str:
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {sorted(KINDS)}")
        content = content.strip()
        if len(content) < 8:
            raise ValueError("content too short")
        if len(content) > 4000:
            content = content[:4000]
        mid = _mem_id()
        now = _now()
        async with self._sessions() as session:
            if supersedes:
                old = await session.get(MemoryEntryRow, supersedes)
                if old and old.tenant_id == tenant_id:
                    old.superseded_by = mid
                    old.updated_at = now
            session.add(
                MemoryEntryRow(
                    id=mid,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    project_key=project_key.upper() if project_key else None,
                    kind=kind,
                    content=content,
                    source_run_id=source_run_id,
                    confidence=max(0.0, min(1.0, confidence)),
                    expires_at=now + timedelta(days=ttl_days),
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.commit()
        return mid

    async def search(
        self,
        *,
        tenant_id: str,
        query: str,
        project_key: str | None = None,
        limit: int = 8,
    ) -> list[dict[str, Any]]:
        q = query.strip().lower()
        now = _now()
        async with self._sessions() as session:
            stmt = select(MemoryEntryRow).where(
                MemoryEntryRow.tenant_id == tenant_id,
                MemoryEntryRow.superseded_by.is_(None),
                or_(MemoryEntryRow.expires_at.is_(None), MemoryEntryRow.expires_at > now),
            )
            if project_key:
                stmt = stmt.where(
                    or_(
                        MemoryEntryRow.project_key == project_key.upper(),
                        MemoryEntryRow.project_key.is_(None),
                    )
                )
            stmt = stmt.order_by(MemoryEntryRow.updated_at.desc()).limit(limit * 4)
            rows = list((await session.execute(stmt)).scalars())
        scored: list[tuple[int, MemoryEntryRow]] = []
        tokens = [t for t in q.split() if len(t) > 2] or [q]
        for row in rows:
            text = row.content.lower()
            score = sum(1 for t in tokens if t in text)
            if score or not tokens:
                scored.append((score, row))
        scored.sort(key=lambda x: (-x[0], -x[1].confidence))
        out = []
        for score, row in scored[:limit]:
            out.append(
                {
                    "id": row.id,
                    "kind": row.kind,
                    "content": row.content,
                    "project_key": row.project_key,
                    "confidence": row.confidence,
                    "source_run_id": row.source_run_id,
                    "updated_at": row.updated_at.isoformat(),
                    "score": score,
                }
            )
        return out

    async def remember_run_summary(
        self,
        *,
        tenant_id: str,
        user_id: str,
        project_key: str,
        run_id: str,
        question: str,
        answer: str,
    ) -> str | None:
        """Persist a short durable summary after a successful ask. Never stores live status as gospel."""
        snippet = answer.strip().replace("\n", " ")
        if len(snippet) > 600:
            snippet = snippet[:600] + "…"
        content = f"Prior Q: {question[:200]}\nPrior A: {snippet}"
        return await self.write(
            tenant_id=tenant_id,
            user_id=user_id,
            project_key=project_key,
            kind="summary",
            content=content,
            source_run_id=run_id,
            confidence=0.55,
            ttl_days=60,
        )
