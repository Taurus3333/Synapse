"""Ask a question via the bounded agent. Tenant bound from JWT. STM run persisted.

Also exposes LLM-free /v1/multihop for gather→follow→pack verification.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from synapse.agent.integrate import run_multihop_integration
from synapse.agent.runner import run_agent
from synapse.auth.deps import get_principal
from synapse.auth.principal import Principal
from synapse.guardrails.input_policy import check_user_question
from synapse.memory.ltm import LongTermMemory
from synapse.memory.stm import ShortTermMemory
from synapse.obs.metrics import get_metrics
from synapse.obs.tracing import timed_span
from synapse.platform import live as repo
from synapse.platform.config import get_settings
from synapse.platform.logging import get_logger
from synapse.rag.embeddings import Embedder
from synapse.tools.runtime import ToolSession

logger = get_logger(__name__)
router = APIRouter(prefix="/v1", tags=["agent"])


class AskBody(BaseModel):
    question: str = Field(min_length=5, max_length=2000)
    project_key: str = Field(default="ATLAS", max_length=32)


async def _authorize_project(request: Request, principal: Principal, project_key: str) -> str:
    key = project_key.upper()
    async with request.app.state.sessions() as session:
        project = await repo.get_project(session, principal.tenant_id, key)
        if project is None:
            raise HTTPException(404, "project not found")
        if not principal.is_tenant_admin:
            ok = await repo.user_on_project(
                session, principal.tenant_id, project.id, principal.user_id
            )
            if not ok:
                raise HTTPException(403, "not a member of this project")
    return key


def _tools(request: Request, principal: Principal, ltm: LongTermMemory | None = None) -> ToolSession:
    settings = get_settings()
    embedder = None
    if settings.openai_api_key:
        embedder = Embedder(settings.openai_api_key.get_secret_value())
    return ToolSession(
        principal=principal,
        sessions=request.app.state.sessions,
        embedder=embedder,
        ltm=ltm,
    )


@router.post("/ask")
async def ask(
    body: AskBody,
    request: Request,
    principal: Principal = Depends(get_principal),
) -> dict:
    project_key = await _authorize_project(request, principal, body.project_key)

    verdict = check_user_question(body.question)
    if not verdict.allowed:
        get_metrics().incr("asks_total", outcome="guardrail_blocked")
        logger.info(
            "ask_guardrail_blocked",
            project_key=project_key,
            reason=verdict.reason,
            flags=list(verdict.flags),
        )
        raise HTTPException(
            400,
            {
                "error": "guardrail_blocked",
                "reason": verdict.reason,
                "flags": list(verdict.flags),
            },
        )

    stm = ShortTermMemory(request.app.state.sessions)
    ltm = LongTermMemory(request.app.state.sessions)
    run_id = await stm.start_run(
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        project_key=project_key,
        question=body.question,
    )
    if verdict.flags:
        await stm.checkpoint(
            run_id,
            tenant_id=principal.tenant_id,
            phase="guardrail",
            payload={"flags": list(verdict.flags), "soft": verdict.soft},
        )

    tools = _tools(request, principal, ltm)
    started = time.perf_counter()
    try:
        with timed_span("ask", run_id=run_id, project_key=project_key):
            result = await run_agent(
                body.question,
                tools,
                project_key=project_key,
                stm=stm,
                run_id=run_id,
            )
    except Exception as exc:
        get_metrics().incr("asks_total", outcome="error")
        get_metrics().observe(
            "ask_duration_ms",
            (time.perf_counter() - started) * 1000.0,
            outcome="error",
        )
        await stm.fail(run_id, tenant_id=principal.tenant_id, error=str(exc))
        if isinstance(exc, RuntimeError):
            raise HTTPException(503, str(exc)) from exc
        raise

    get_metrics().incr("asks_total", outcome="ok")
    get_metrics().observe(
        "ask_duration_ms",
        (time.perf_counter() - started) * 1000.0,
        outcome="ok",
    )
    get_metrics().incr("ask_tool_calls_total", amount=tools.call_count)

    memory_id = None
    try:
        memory_id = await ltm.remember_run_summary(
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            project_key=project_key,
            run_id=run_id,
            question=body.question,
            answer=result.answer,
        )
    except Exception:
        memory_id = None

    return {
        "run_id": run_id,
        "memory_id": memory_id,
        "answer": result.answer,
        "citations": result.citations,
        "gaps": result.gaps,
        "conflicts": result.conflicts,
        "rejected_citations": result.rejected_citations,
        "plan": result.plan,
        "checklist": result.checklist,
        "hops": result.hops,
        "tool_trail": result.tool_trail,
        "usage": result.usage,
        "guardrail": {"flags": list(verdict.flags), "soft": verdict.soft},
        "principal": {
            "user_id": principal.user_id,
            "tenant_id": principal.tenant_id,
            "role": principal.role,
        },
    }


@router.post("/multihop")
async def multihop(
    body: AskBody,
    request: Request,
    principal: Principal = Depends(get_principal),
) -> dict:
    """LLM-free gather→follow→evidence pack. Proves Chunk 11 integration without a chat model."""
    project_key = await _authorize_project(request, principal, body.project_key)
    ltm = LongTermMemory(request.app.state.sessions)
    tools = _tools(request, principal, ltm)
    tools.max_calls = 22
    with timed_span("multihop", project_key=project_key, metric="multihop_duration_ms"):
        result = await run_multihop_integration(
            tools, project_key=project_key, question=body.question
        )
    get_metrics().incr("multihop_total", outcome="ok")
    pack = result.pack
    return {
        "project_key": result.project_key,
        "hops": result.hops,
        "tool_trail": result.tool_trail,
        "sources_present": result.sources_present,
        "external_providers": result.external_providers,
        "live_status": pack.live_status if pack else None,
        "item_count": len(pack.items) if pack else 0,
        "conflicts": [
            {"document_id": c.document_id, "live_status": c.live_status, "detail": c.detail}
            for c in (pack.conflicts if pack else [])
        ],
        "gaps": list(pack.gaps) if pack else [],
        "principal": {
            "user_id": principal.user_id,
            "tenant_id": principal.tenant_id,
            "role": principal.role,
        },
    }
