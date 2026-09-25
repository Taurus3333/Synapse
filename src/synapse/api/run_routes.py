"""Inspect durable agent runs, checkpoints, and failed-run DLQ-lite."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from synapse.auth.deps import get_principal
from synapse.auth.principal import Principal
from synapse.memory.stm import ShortTermMemory
from synapse.reliability.dlq import DeadLetterLite

router = APIRouter(prefix="/v1/runs", tags=["runs"])


@router.get("/failed")
async def list_failed_runs(
    request: Request,
    principal: Principal = Depends(get_principal),
    include_acked: bool = False,
) -> dict:
    """DLQ-lite: failed sync asks awaiting operator review."""
    dlq = DeadLetterLite(request.app.state.sessions)
    items = await dlq.list_failed(
        tenant_id=principal.tenant_id, include_acked=include_acked
    )
    return {"items": items, "count": len(items)}


@router.post("/{run_id}/acknowledge")
async def acknowledge_failed_run(
    run_id: str,
    request: Request,
    principal: Principal = Depends(get_principal),
) -> dict:
    dlq = DeadLetterLite(request.app.state.sessions)
    ok = await dlq.acknowledge(tenant_id=principal.tenant_id, run_id=run_id)
    if not ok:
        raise HTTPException(404, "failed run not found")
    return {"id": run_id, "dlq_acked": True}


@router.get("/{run_id}")
async def get_run(
    run_id: str,
    request: Request,
    principal: Principal = Depends(get_principal),
) -> dict:
    stm = ShortTermMemory(request.app.state.sessions)
    run = await stm.get_run(tenant_id=principal.tenant_id, run_id=run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    return {
        "id": run.id,
        "project_key": run.project_key,
        "question": run.question,
        "status": run.status,
        "phase": run.phase,
        "plan": run.plan,
        "checklist": run.checklist,
        "answer": run.answer,
        "citations": run.citations,
        "gaps": run.gaps,
        "conflicts": run.conflicts,
        "tool_trail": run.tool_trail,
        "usage": run.usage,
        "error": run.error,
        "created_at": run.created_at.isoformat(),
        "updated_at": run.updated_at.isoformat(),
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }


@router.get("/{run_id}/checkpoints")
async def get_checkpoints(
    run_id: str,
    request: Request,
    principal: Principal = Depends(get_principal),
) -> list[dict]:
    stm = ShortTermMemory(request.app.state.sessions)
    run = await stm.get_run(tenant_id=principal.tenant_id, run_id=run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    rows = await stm.list_checkpoints(tenant_id=principal.tenant_id, run_id=run_id)
    return [
        {
            "id": c.id,
            "step_index": c.step_index,
            "phase": c.phase,
            "payload": c.payload,
            "created_at": c.created_at.isoformat(),
        }
        for c in rows
    ]
