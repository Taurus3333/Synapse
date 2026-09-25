"""LTM HTTP surface for the demo UI — write/search durable notes (never overrides live)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from synapse.auth.deps import get_principal
from synapse.auth.principal import Principal
from synapse.memory.ltm import LongTermMemory

router = APIRouter(prefix="/v1/memory", tags=["memory"])


class MemoryWriteBody(BaseModel):
    content: str = Field(min_length=8, max_length=4000)
    kind: str = Field(default="fact", max_length=32)
    project_key: str | None = Field(default=None, max_length=32)
    confidence: float = Field(default=0.75, ge=0.0, le=1.0)


@router.post("")
async def write_memory(
    body: MemoryWriteBody,
    request: Request,
    principal: Principal = Depends(get_principal),
) -> dict:
    ltm = LongTermMemory(request.app.state.sessions)
    try:
        mid = await ltm.write(
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            project_key=body.project_key,
            kind=body.kind,
            content=body.content,
            confidence=body.confidence,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"id": mid, "kind": body.kind, "project_key": body.project_key}


@router.get("/search")
async def search_memory(
    request: Request,
    q: str = Query(min_length=2, max_length=500),
    project_key: str | None = Query(default=None, max_length=32),
    limit: int = Query(default=5, ge=1, le=20),
    principal: Principal = Depends(get_principal),
) -> dict:
    ltm = LongTermMemory(request.app.state.sessions)
    hits = await ltm.search(
        tenant_id=principal.tenant_id,
        query=q,
        project_key=project_key,
        limit=limit,
    )
    return {"query": q, "hits": hits}
