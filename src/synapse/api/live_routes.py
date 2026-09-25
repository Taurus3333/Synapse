"""Live enterprise HTTP API. Tenant identity comes from JWT only."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from synapse.auth.deps import get_principal, require_project_access, require_writer
from synapse.auth.principal import Principal
from synapse.platform import live as repo
from synapse.platform.config import get_settings
from synapse.rag.embeddings import Embedder
from synapse.rag.retrieve import retrieve

router = APIRouter(prefix="/v1", tags=["live"])


class StatusBody(BaseModel):
    status: str = Field(min_length=1, max_length=32)


class RiskBody(BaseModel):
    title: str = Field(min_length=1, max_length=256)
    description: str = Field(min_length=1, max_length=4000)
    severity: str = Field(default="high", max_length=32)


class BlockerBody(BaseModel):
    title: str = Field(min_length=1, max_length=256)
    description: str = Field(min_length=1, max_length=4000)


@router.get("/projects")
async def projects(
    request: Request, principal: Principal = Depends(get_principal)
) -> list[dict]:
    async with request.app.state.sessions() as session:
        rows = await repo.list_projects(session, principal.tenant_id)
    return [
        {
            "id": p.id,
            "key": p.key,
            "name": p.name,
            "status": p.status,
            "priority": p.priority,
            "updated_at": p.updated_at.isoformat(),
        }
        for p in rows
    ]


@router.get("/projects/{key}")
async def project(
    key: str,
    request: Request,
    principal: Principal = Depends(require_project_access),
) -> dict:
    async with request.app.state.sessions() as session:
        row = await repo.get_project(session, principal.tenant_id, key.upper())
    assert row is not None
    return {
        "id": row.id,
        "key": row.key,
        "name": row.name,
        "description": row.description,
        "status": row.status,
        "priority": row.priority,
        "owner_user_id": row.owner_user_id,
        "start_date": row.start_date.isoformat(),
        "target_date": row.target_date.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


@router.patch("/projects/{key}/status")
async def patch_project_status(
    key: str,
    body: StatusBody,
    request: Request,
    principal: Principal = Depends(require_writer),
    _: Principal = Depends(require_project_access),
) -> dict:
    async with request.app.state.sessions() as session:
        row = await repo.update_project_status(
            session, principal.tenant_id, key.upper(), body.status, principal.user_id
        )
    if row is None:
        raise HTTPException(404, "project not found")
    return {"id": row.id, "key": row.key, "status": row.status, "updated_at": row.updated_at.isoformat()}


@router.get("/projects/{key}/tasks")
async def tasks(
    key: str,
    request: Request,
    principal: Principal = Depends(require_project_access),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[dict]:
    async with request.app.state.sessions() as session:
        project_row = await repo.get_project(session, principal.tenant_id, key.upper())
        assert project_row is not None
        rows = await repo.list_tasks(session, principal.tenant_id, project_row.id, limit=limit)
    return [
        {
            "id": t.id,
            "key": t.key,
            "title": t.title,
            "status": t.status,
            "priority": t.priority,
            "due_date": t.due_date.isoformat() if t.due_date else None,
            "assignee_id": t.assignee_id,
        }
        for t in rows
    ]


@router.patch("/tasks/{task_id}/status")
async def patch_task(
    task_id: str,
    body: StatusBody,
    request: Request,
    principal: Principal = Depends(require_writer),
) -> dict:
    async with request.app.state.sessions() as session:
        row = await repo.update_task_status(
            session, principal.tenant_id, task_id, body.status, principal.user_id
        )
    if row is None:
        raise HTTPException(404, "task not found")
    return {"id": row.id, "key": row.key, "status": row.status}


@router.get("/projects/{key}/risks")
async def risks(
    key: str,
    request: Request,
    principal: Principal = Depends(require_project_access),
) -> list[dict]:
    async with request.app.state.sessions() as session:
        project_row = await repo.get_project(session, principal.tenant_id, key.upper())
        assert project_row is not None
        rows = await repo.list_risks(session, principal.tenant_id, project_row.id)
    return [
        {
            "id": r.id,
            "title": r.title,
            "severity": r.severity,
            "status": r.status,
            "identified_at": r.identified_at.isoformat(),
        }
        for r in rows
    ]


@router.post("/projects/{key}/risks", status_code=201)
async def post_risk(
    key: str,
    body: RiskBody,
    request: Request,
    principal: Principal = Depends(require_writer),
    _: Principal = Depends(require_project_access),
) -> dict:
    async with request.app.state.sessions() as session:
        row = await repo.create_risk(
            session,
            principal.tenant_id,
            key.upper(),
            title=body.title,
            description=body.description,
            severity=body.severity,
            actor_id=principal.user_id,
        )
    if row is None:
        raise HTTPException(404, "project not found")
    return {"id": row.id, "title": row.title, "status": row.status, "severity": row.severity}


@router.get("/projects/{key}/blockers")
async def blockers(
    key: str,
    request: Request,
    principal: Principal = Depends(require_project_access),
) -> list[dict]:
    async with request.app.state.sessions() as session:
        project_row = await repo.get_project(session, principal.tenant_id, key.upper())
        assert project_row is not None
        rows = await repo.list_blockers(session, principal.tenant_id, project_row.id)
    return [
        {
            "id": b.id,
            "title": b.title,
            "status": b.status,
            "opened_at": b.opened_at.isoformat(),
        }
        for b in rows
    ]


@router.post("/projects/{key}/blockers", status_code=201)
async def post_blocker(
    key: str,
    body: BlockerBody,
    request: Request,
    principal: Principal = Depends(require_writer),
    _: Principal = Depends(require_project_access),
) -> dict:
    async with request.app.state.sessions() as session:
        row = await repo.create_blocker(
            session,
            principal.tenant_id,
            key.upper(),
            title=body.title,
            description=body.description,
            actor_id=principal.user_id,
        )
    if row is None:
        raise HTTPException(404, "project not found")
    return {"id": row.id, "title": row.title, "status": row.status}


@router.get("/projects/{key}/activity")
async def activity(
    key: str,
    request: Request,
    principal: Principal = Depends(require_project_access),
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict]:
    async with request.app.state.sessions() as session:
        project_row = await repo.get_project(session, principal.tenant_id, key.upper())
        assert project_row is not None
        rows = await repo.list_activity(
            session, principal.tenant_id, project_row.id, since=since, until=until, limit=limit
        )
    return [
        {
            "id": a.id,
            "event_type": a.event_type,
            "entity_type": a.entity_type,
            "entity_id": a.entity_id,
            "occurred_at": a.occurred_at.isoformat(),
            "summary": a.summary,
        }
        for a in rows
    ]


@router.get("/projects/{key}/search")
async def search_docs(
    key: str,
    request: Request,
    q: str = Query(min_length=2),
    principal: Principal = Depends(require_project_access),
    limit: int = Query(default=5, ge=1, le=20),
) -> list[dict]:
    settings = get_settings()
    if not settings.openai_api_key:
        raise HTTPException(503, "SYNAPSE_OPENAI_API_KEY required for retrieval")
    embedder = Embedder(settings.openai_api_key.get_secret_value())
    async with request.app.state.sessions() as session:
        project_row = await repo.get_project(session, principal.tenant_id, key.upper())
        assert project_row is not None
        hits = await retrieve(
            session,
            embedder,
            tenant_id=principal.tenant_id,
            query=q,
            project_id=project_row.id,
            limit=limit,
        )
    return [
        {
            "chunk_id": h.chunk_id,
            "document_id": h.document_id,
            "doc_type": h.doc_type,
            "authored_at": h.authored_at,
            "text": h.text,
            "stale_vs_live": h.stale_vs_live,
            "framed": h.framed,
        }
        for h in hits
    ]
