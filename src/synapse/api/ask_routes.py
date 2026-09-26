"""Ask a question via the bounded agent. Tenant bound from JWT. STM run persisted.

Also exposes LLM-free /v1/multihop for gather→follow→pack verification.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from synapse.agent.graph import AgentBudgets
from synapse.agent.integrate import run_multihop_integration
from synapse.agent.runner import run_agent
from synapse.auth.deps import get_principal
from synapse.auth.principal import Principal
from synapse.guardrails.input_policy import check_user_question
from synapse.memory.ltm import LongTermMemory
from synapse.memory.stm import BadIdempotencyKey, ShortTermMemory
from synapse.obs.metrics import get_metrics
from synapse.obs.tracing import timed_span
from synapse.platform import live as repo
from synapse.platform.config import get_settings
from synapse.platform.logging import get_logger
from synapse.rag.embeddings import Embedder
from synapse.reliability.admission import AdmissionFull
from synapse.reliability.circuit import CircuitOpen
from synapse.reliability.deadline import AskDeadlineExceeded
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


def _tools(
    request: Request, principal: Principal, ltm: LongTermMemory | None = None
) -> ToolSession:
    settings = get_settings()
    embedder = None
    if settings.openai_api_key:
        embedder = Embedder(
            settings.openai_api_key.get_secret_value(),
            timeout_s=settings.embed_timeout_s,
        )
    return ToolSession(
        principal=principal,
        sessions=request.app.state.sessions,
        embedder=embedder,
        ltm=ltm,
    )


def _idempotency_key(request: Request) -> str | None:
    raw = request.headers.get("idempotency-key")
    if raw is None or not raw.strip():
        return None
    return raw.strip()


def _fail_code(exc: BaseException) -> str:
    if isinstance(exc, AskDeadlineExceeded):
        return "ask_deadline_exceeded"
    if isinstance(exc, asyncio.CancelledError):
        return "ask_cancelled"
    if isinstance(exc, CircuitOpen):
        return "llm_unavailable"
    text = str(exc)
    if isinstance(exc, RuntimeError) and "budget" in text:
        return "tool_budget_exceeded"
    if isinstance(exc, RuntimeError) and "API_KEY" in text:
        return "llm_unavailable"
    return "ask_failed"


async def _fail_ask(stm: ShortTermMemory, run_id: str, tenant_id: str, exc: BaseException) -> str:
    code = _fail_code(exc)
    label = f"{code}:{type(exc).__name__}"
    await stm.fail(run_id, tenant_id=tenant_id, error=label[:4000])
    return code


@router.post("/ask", response_model=None)
async def ask(
    body: AskBody,
    request: Request,
    principal: Principal = Depends(get_principal),
) -> dict | JSONResponse:
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

    settings = request.app.state.settings
    try:
        async with request.app.state.admission.acquire(principal.tenant_id):
            return await _execute_ask(
                request,
                body,
                principal,
                project_key,
                verdict_flags=list(verdict.flags),
                verdict_soft=verdict.soft,
                deadline_s=settings.ask_deadline_s,
                chat_timeout_s=settings.chat_timeout_s,
            )
    except AdmissionFull as exc:
        get_metrics().incr("asks_total", outcome="rejected_capacity")
        logger.info("ask_capacity", scope=exc.scope, project_key=project_key)
        raise HTTPException(
            429,
            {"error": "ask_capacity", "scope": exc.scope},
            headers={"Retry-After": "5"},
        ) from exc


async def _execute_ask(
    request: Request,
    body: AskBody,
    principal: Principal,
    project_key: str,
    *,
    verdict_flags: list[str],
    verdict_soft: bool,
    deadline_s: float,
    chat_timeout_s: float,
    on_event: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> dict | JSONResponse:
    stm = ShortTermMemory(request.app.state.sessions)
    ltm = LongTermMemory(request.app.state.sessions)
    try:
        claim = await stm.claim_run(
            tenant_id=principal.tenant_id,
            user_id=principal.user_id,
            project_key=project_key,
            question=body.question,
            idempotency_key=_idempotency_key(request),
        )
    except BadIdempotencyKey as exc:
        raise HTTPException(400, {"error": "bad_idempotency_key"}) from exc

    if claim.action == "replay":
        get_metrics().incr("asks_total", outcome="replayed")
        return JSONResponse(
            content=claim.response or {"run_id": claim.run_id, "replayed": True},
            headers={"Idempotent-Replayed": "true"},
        )
    if claim.action == "in_progress":
        raise HTTPException(
            409,
            {"error": "idempotency_in_progress", "run_id": claim.run_id},
            headers={"Retry-After": "2"},
        )
    if claim.action == "mismatch":
        raise HTTPException(
            422,
            {"error": "idempotency_key_reused", "run_id": claim.run_id},
        )

    run_id = claim.run_id
    if verdict_flags:
        await stm.checkpoint(
            run_id,
            tenant_id=principal.tenant_id,
            phase="guardrail",
            payload={"flags": verdict_flags, "soft": verdict_soft},
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
                budgets=AgentBudgets(deadline_s=deadline_s, chat_timeout_s=chat_timeout_s),
                on_event=on_event,
            )
    except asyncio.CancelledError as exc:
        get_metrics().incr("asks_total", outcome="cancelled")
        await asyncio.shield(_fail_ask(stm, run_id, principal.tenant_id, exc))
        raise
    except Exception as exc:
        code = await _fail_ask(stm, run_id, principal.tenant_id, exc)
        get_metrics().incr("asks_total", outcome=code)
        get_metrics().observe(
            "ask_duration_ms",
            (time.perf_counter() - started) * 1000.0,
            outcome="error",
        )
        logger.warning("ask_failed", run_id=run_id, error=code, error_type=type(exc).__name__)
        raise HTTPException(503, {"error": code, "run_id": run_id}) from exc

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

    payload = {
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
        "evidence": result.evidence,
        "guardrail": {
            "input": "allowed",
            "flags": verdict_flags,
            "soft": verdict_soft,
            "citation_check": "grounded",
            "rejected_citation_count": len(result.rejected_citations),
        },
        "principal": {
            "user_id": principal.user_id,
            "tenant_id": principal.tenant_id,
            "role": principal.role,
        },
    }
    await stm.save_response(run_id, tenant_id=principal.tenant_id, response=payload)
    return payload


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload, default=str)}\n\n"


async def _stream_events(
    request: Request,
    body: AskBody,
    principal: Principal,
    project_key: str,
    *,
    verdict_flags: list[str],
    verdict_soft: bool,
) -> AsyncIterator[str]:
    """Same ask as POST /v1/ask. Events are node names and counts, not model text."""
    settings = request.app.state.settings
    yield _sse(
        {
            "type": "event",
            "step": "accepted",
            "label": "Request accepted",
            "detail": {"project_key": project_key},
        }
    )
    if verdict_flags:
        yield _sse(
            {
                "type": "event",
                "step": "guardrail",
                "label": "Input guardrail",
                "detail": {"flags": verdict_flags, "soft": verdict_soft},
            }
        )
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def on_event(event: dict[str, Any]) -> None:
        await queue.put(event)

    task = asyncio.create_task(
        _execute_ask(
            request,
            body,
            principal,
            project_key,
            verdict_flags=verdict_flags,
            verdict_soft=verdict_soft,
            deadline_s=settings.ask_deadline_s,
            chat_timeout_s=settings.chat_timeout_s,
            on_event=on_event,
        )
    )
    try:
        while not task.done():
            try:
                event = await asyncio.wait_for(queue.get(), timeout=0.25)
            except TimeoutError:
                continue
            yield _sse({"type": "event", **event})
        while not queue.empty():
            yield _sse({"type": "event", **queue.get_nowait()})
        try:
            result = task.result()
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {"error": str(exc.detail)}
            yield _sse({"type": "error", "status": exc.status_code, "detail": detail})
            return
        if isinstance(result, JSONResponse):
            raw = result.body
            text = raw.decode() if isinstance(raw, bytes | bytearray) else bytes(raw).decode()
            yield _sse({"type": "done", "result": json.loads(text), "replayed": True})
            return
        yield _sse({"type": "done", "result": result})
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass


@router.post("/ask/stream")
async def ask_stream(
    body: AskBody,
    request: Request,
    principal: Principal = Depends(get_principal),
) -> StreamingResponse:
    project_key = await _authorize_project(request, principal, body.project_key)
    verdict = check_user_question(body.question)
    if not verdict.allowed:
        get_metrics().incr("asks_total", outcome="guardrail_blocked")
        raise HTTPException(
            400,
            {
                "error": "guardrail_blocked",
                "reason": verdict.reason,
                "flags": list(verdict.flags),
            },
        )
    admission = request.app.state.admission
    held = admission.acquire(principal.tenant_id)
    try:
        await held.__aenter__()
    except AdmissionFull as exc:
        get_metrics().incr("asks_total", outcome="rejected_capacity")
        raise HTTPException(
            429,
            {"error": "ask_capacity", "scope": exc.scope},
            headers={"Retry-After": "5"},
        ) from exc

    async def body_iter() -> AsyncIterator[str]:
        try:
            async for chunk in _stream_events(
                request,
                body,
                principal,
                project_key,
                verdict_flags=list(verdict.flags),
                verdict_soft=verdict.soft,
            ):
                yield chunk
        finally:
            await held.__aexit__(None, None, None)

    return StreamingResponse(body_iter(), media_type="text/event-stream")


@router.post("/multihop")
async def multihop(
    body: AskBody,
    request: Request,
    principal: Principal = Depends(get_principal),
) -> dict:
    """LLM-free gather→follow→evidence pack. Proves Chunk 11 integration without a chat model."""
    project_key = await _authorize_project(request, principal, body.project_key)
    try:
        async with request.app.state.admission.acquire(principal.tenant_id):
            return await _execute_multihop(request, body, principal, project_key)
    except AdmissionFull as exc:
        get_metrics().incr("multihop_total", outcome="rejected_capacity")
        raise HTTPException(
            429,
            {"error": "ask_capacity", "scope": exc.scope},
            headers={"Retry-After": "5"},
        ) from exc


async def _execute_multihop(
    request: Request, body: AskBody, principal: Principal, project_key: str
) -> dict:
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
