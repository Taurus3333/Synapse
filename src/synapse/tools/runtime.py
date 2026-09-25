"""Typed tool contracts. Tenant/user never accepted as tool args — bound at session start."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from synapse.auth.principal import Principal
from synapse.memory.ltm import LongTermMemory
from synapse.obs.metrics import get_metrics
from synapse.platform import live as repo
from synapse.rag.embeddings import Embedder
from synapse.rag.retrieve import retrieve
from synapse.tools import connectors


@dataclass
class ToolSession:
    principal: Principal
    sessions: async_sessionmaker[AsyncSession]
    embedder: Embedder | None = None
    ltm: LongTermMemory | None = None
    call_count: int = 0
    max_calls: int = 50
    audit: list[dict[str, Any]] = field(default_factory=list)

    def _bump(self, name: str) -> None:
        if self.call_count >= self.max_calls:
            raise RuntimeError("tool call budget exceeded")
        self.call_count += 1
        self.audit.append({"tool": name, "n": self.call_count})


class ProjectKeyIn(BaseModel):
    project_key: str = Field(min_length=1, max_length=32)


class SearchIn(BaseModel):
    project_key: str
    query: str = Field(min_length=2, max_length=500)
    limit: int = Field(default=5, ge=1, le=20)


class WindowIn(BaseModel):
    project_key: str
    since: datetime | None = None
    until: datetime | None = None
    limit: int = Field(default=50, ge=1, le=200)


class ExtSearchIn(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    limit: int = Field(default=5, ge=1, le=10)


class MemorySearchIn(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    project_key: str | None = None
    limit: int = Field(default=5, ge=1, le=20)


class MemoryWriteIn(BaseModel):
    content: str = Field(min_length=8, max_length=4000)
    kind: str = Field(default="fact", max_length=32)
    project_key: str | None = None
    confidence: float = Field(default=0.6, ge=0.0, le=1.0)


async def project_lookup(session: ToolSession, args: ProjectKeyIn) -> dict[str, Any]:
    session._bump("project_lookup")
    async with session.sessions() as db:
        row = await repo.get_project(db, session.principal.tenant_id, args.project_key.upper())
    if row is None:
        return {"found": False}
    return {
        "found": True,
        "id": row.id,
        "key": row.key,
        "name": row.name,
        "status": row.status,
        "priority": row.priority,
        "updated_at": row.updated_at.isoformat(),
    }


async def task_search(session: ToolSession, args: ProjectKeyIn) -> dict[str, Any]:
    session._bump("task_search")
    async with session.sessions() as db:
        project = await repo.get_project(db, session.principal.tenant_id, args.project_key.upper())
        if project is None:
            return {"tasks": []}
        rows = await repo.list_tasks(db, session.principal.tenant_id, project.id, limit=50)
    return {
        "tasks": [
            {
                "id": t.id,
                "key": t.key,
                "title": t.title,
                "status": t.status,
                "due_date": t.due_date.isoformat() if t.due_date else None,
            }
            for t in rows
        ]
    }


async def risk_list(session: ToolSession, args: ProjectKeyIn) -> dict[str, Any]:
    session._bump("risk_list")
    async with session.sessions() as db:
        project = await repo.get_project(db, session.principal.tenant_id, args.project_key.upper())
        if project is None:
            return {"risks": []}
        rows = await repo.list_risks(db, session.principal.tenant_id, project.id)
    return {
        "risks": [
            {
                "id": r.id,
                "title": r.title,
                "severity": r.severity,
                "status": r.status,
                "identified_at": r.identified_at.isoformat(),
            }
            for r in rows
        ]
    }


async def blocker_list(session: ToolSession, args: ProjectKeyIn) -> dict[str, Any]:
    session._bump("blocker_list")
    async with session.sessions() as db:
        project = await repo.get_project(db, session.principal.tenant_id, args.project_key.upper())
        if project is None:
            return {"blockers": []}
        rows = await repo.list_blockers(db, session.principal.tenant_id, project.id)
    return {
        "blockers": [
            {
                "id": b.id,
                "title": b.title,
                "status": b.status,
                "opened_at": b.opened_at.isoformat(),
            }
            for b in rows
        ]
    }


async def project_activity(session: ToolSession, args: WindowIn) -> dict[str, Any]:
    session._bump("project_activity")
    async with session.sessions() as db:
        project = await repo.get_project(db, session.principal.tenant_id, args.project_key.upper())
        if project is None:
            return {"events": []}
        rows = await repo.list_activity(
            db,
            session.principal.tenant_id,
            project.id,
            since=args.since,
            until=args.until,
            limit=args.limit,
        )
    return {
        "events": [
            {
                "id": e.id,
                "event_type": e.event_type,
                "summary": e.summary,
                "occurred_at": e.occurred_at.isoformat(),
            }
            for e in rows
        ]
    }


async def document_search(session: ToolSession, args: SearchIn) -> dict[str, Any]:
    session._bump("document_search")
    if session.embedder is None:
        return {"error": "embeddings unavailable", "hits": []}
    async with session.sessions() as db:
        project = await repo.get_project(db, session.principal.tenant_id, args.project_key.upper())
        if project is None:
            return {"hits": []}
        hits = await retrieve(
            db,
            session.embedder,
            tenant_id=session.principal.tenant_id,
            query=args.query,
            project_id=project.id,
            limit=args.limit,
        )
    return {
        "hits": [
            {
                "chunk_id": h.chunk_id,
                "document_id": h.document_id,
                "text": h.text,
                "authored_at": h.authored_at,
                "framed": h.framed,
                "stale_vs_live": h.stale_vs_live,
                "doc_type": h.doc_type,
            }
            for h in hits
        ]
    }


async def email_search(session: ToolSession, args: SearchIn) -> dict[str, Any]:
    session._bump("email_search")
    async with session.sessions() as db:
        project = await repo.get_project(db, session.principal.tenant_id, args.project_key.upper())
        if project is None:
            return {"emails": []}
        rows = await repo.search_emails(
            db, session.principal.tenant_id, project.id, query=args.query, limit=args.limit
        )
    return {
        "emails": [
            {
                "id": e.id,
                "subject": e.subject,
                "sent_at": e.sent_at.isoformat(),
                "body": e.body[:1500],
            }
            for e in rows
        ]
    }


async def meeting_search(session: ToolSession, args: SearchIn) -> dict[str, Any]:
    session._bump("meeting_search")
    async with session.sessions() as db:
        project = await repo.get_project(db, session.principal.tenant_id, args.project_key.upper())
        if project is None:
            return {"meetings": []}
        rows = await repo.search_meetings(
            db, session.principal.tenant_id, project.id, query=args.query, limit=args.limit
        )
    return {
        "meetings": [
            {
                "id": m.id,
                "title": m.title,
                "started_at": m.started_at.isoformat(),
                "notes": m.notes[:1500],
            }
            for m in rows
        ]
    }


async def github_search(session: ToolSession, args: ExtSearchIn) -> dict[str, Any]:
    session._bump("github_search")
    return await connectors.github_search(query=args.query, limit=args.limit)


async def slack_search(session: ToolSession, args: ExtSearchIn) -> dict[str, Any]:
    session._bump("slack_search")
    return await connectors.slack_search(query=args.query, limit=args.limit)


async def gmail_search(session: ToolSession, args: ExtSearchIn) -> dict[str, Any]:
    session._bump("gmail_search")
    return await connectors.gmail_search(query=args.query, limit=args.limit)


async def hn_search(session: ToolSession, args: ExtSearchIn) -> dict[str, Any]:
    session._bump("hn_search")
    return await connectors.hackernews_search(query=args.query, limit=args.limit)


async def stackoverflow_search(session: ToolSession, args: ExtSearchIn) -> dict[str, Any]:
    session._bump("stackoverflow_search")
    return await connectors.stackoverflow_search(query=args.query, limit=args.limit)


async def wikipedia_search(session: ToolSession, args: ExtSearchIn) -> dict[str, Any]:
    session._bump("wikipedia_search")
    return await connectors.wikipedia_search(query=args.query, limit=args.limit)


async def memory_search(session: ToolSession, args: MemorySearchIn) -> dict[str, Any]:
    session._bump("memory_search")
    if session.ltm is None:
        return {"error": "ltm unavailable", "memories": []}
    memories = await session.ltm.search(
        tenant_id=session.principal.tenant_id,
        query=args.query,
        project_key=args.project_key,
        limit=args.limit,
    )
    return {"memories": memories}


async def memory_write(session: ToolSession, args: MemoryWriteIn) -> dict[str, Any]:
    session._bump("memory_write")
    if session.ltm is None:
        return {"error": "ltm unavailable"}
    if not session.principal.can_write:
        return {"error": "role cannot write memory"}
    mid = await session.ltm.write(
        tenant_id=session.principal.tenant_id,
        user_id=session.principal.user_id,
        project_key=args.project_key,
        kind=args.kind,
        content=args.content,
        confidence=args.confidence,
    )
    return {"id": mid, "written": True}


_EXTERNAL_TOOLS = {
    "github_search",
    "slack_search",
    "gmail_search",
    "hn_search",
    "stackoverflow_search",
    "wikipedia_search",
}

TOOLS = {
    "project_lookup": (ProjectKeyIn, project_lookup),
    "task_search": (ProjectKeyIn, task_search),
    "risk_list": (ProjectKeyIn, risk_list),
    "blocker_list": (ProjectKeyIn, blocker_list),
    "project_activity": (WindowIn, project_activity),
    "document_search": (SearchIn, document_search),
    "email_search": (SearchIn, email_search),
    "meeting_search": (SearchIn, meeting_search),
    "github_search": (ExtSearchIn, github_search),
    "slack_search": (ExtSearchIn, slack_search),
    "gmail_search": (ExtSearchIn, gmail_search),
    "hn_search": (ExtSearchIn, hn_search),
    "stackoverflow_search": (ExtSearchIn, stackoverflow_search),
    "wikipedia_search": (ExtSearchIn, wikipedia_search),
    "memory_search": (MemorySearchIn, memory_search),
    "memory_write": (MemoryWriteIn, memory_write),
}


async def call_tool(session: ToolSession, name: str, raw: dict[str, Any]) -> dict[str, Any]:
    if name not in TOOLS:
        return {"error": f"unknown tool {name}"}
    schema, fn = TOOLS[name]
    try:
        args = schema.model_validate(raw)
    except Exception as exc:
        return {"error": f"invalid args: {exc}"}
    started = time.perf_counter()
    try:
        result = await fn(session, args)
    except Exception:
        get_metrics().incr("tool_calls_total", tool=name, outcome="error")
        get_metrics().observe(
            "tool_duration_ms",
            (time.perf_counter() - started) * 1000.0,
            tool=name,
            outcome="error",
        )
        raise
    outcome = "error" if isinstance(result, dict) and result.get("error") else "ok"
    get_metrics().incr("tool_calls_total", tool=name, outcome=outcome)
    get_metrics().observe(
        "tool_duration_ms",
        (time.perf_counter() - started) * 1000.0,
        tool=name,
        outcome=outcome,
    )
    return result
