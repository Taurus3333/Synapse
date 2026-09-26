"""Agent entrypoint — LangGraph-backed bounded orchestration."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from typing import Any

from openai import AsyncOpenAI

from synapse.agent.graph import AgentBudgets, AgentResult, GraphContext, get_graph
from synapse.agent.trace import public_events
from synapse.memory.stm import ShortTermMemory
from synapse.reliability.deadline import AskDeadlineExceeded
from synapse.tools.runtime import ToolSession

__all__ = ["AgentBudgets", "AgentResult", "run_agent"]

OnEvent = Callable[[dict[str, Any]], Awaitable[None]]


def _evidence_summary(pack: dict) -> dict:
    """UI-safe counts by source kind — not the full pack prompt."""
    index = pack.get("_items_index") or {}
    by_kind: dict[str, int] = {}
    for meta in index.values():
        kind = str((meta or {}).get("kind") or "unknown")
        # SourceKind.LIVE -> live
        if "." in kind:
            kind = kind.rsplit(".", 1)[-1].lower()
        by_kind[kind] = by_kind.get(kind, 0) + 1
    prompt = pack.get("prompt")
    live_status = None
    if isinstance(prompt, dict):
        live_status = prompt.get("live_status")
    return {
        "live_status": live_status,
        "item_count": int(pack.get("item_count") or len(index) or 0),
        "by_kind": by_kind,
        "conflict_count": len(pack.get("conflicts") or []),
        "gap_count": len(pack.get("gaps") or []),
    }


def _client(timeout_s: float) -> tuple[AsyncOpenAI, str]:
    groq = os.environ.get("SYNAPSE_GROQ_API_KEY") or os.environ.get("GROQ_API_KEY")
    openai_key = os.environ.get("SYNAPSE_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if groq:
        return (
            AsyncOpenAI(
                api_key=groq,
                base_url="https://api.groq.com/openai/v1",
                timeout=timeout_s,
            ),
            "openai/gpt-oss-20b",
        )
    if openai_key:
        return AsyncOpenAI(api_key=openai_key, timeout=timeout_s), "gpt-4o-mini"
    raise RuntimeError("Set SYNAPSE_GROQ_API_KEY or SYNAPSE_OPENAI_API_KEY")


async def _drive(
    graph: Any,
    state: dict[str, Any],
    config: dict[str, Any],
    on_event: OnEvent | None,
) -> dict[str, Any]:
    if on_event is None:
        final = await graph.ainvoke(state, config=config)
        return dict(final)
    merged = dict(state)
    async for chunk in graph.astream(state, config=config, stream_mode="updates"):
        if not isinstance(chunk, dict):
            continue
        for node, delta in chunk.items():
            if not isinstance(delta, dict):
                continue
            merged.update(delta)
            for event in public_events(str(node), delta):
                await on_event(event)
    return merged


async def run_agent(
    question: str,
    tools: ToolSession,
    *,
    project_key: str = "ATLAS",
    budgets: AgentBudgets | None = None,
    stm: ShortTermMemory | None = None,
    run_id: str | None = None,
    on_event: OnEvent | None = None,
) -> AgentResult:
    budgets = budgets or AgentBudgets()
    client, model = _client(budgets.chat_timeout_s)
    tools.max_calls = budgets.max_tool_calls
    ctx = GraphContext(
        tools=tools,
        client=client,
        model=model,
        stm=stm,
        run_id=run_id,
        tenant_id=tools.principal.tenant_id,
    )
    # Share one accumulator so RAG embeds and chat tokens land in the same usage dict.
    if tools.embedder is not None:
        tools.embedder.usage = ctx.usage
    graph = get_graph()
    try:
        final = await asyncio.wait_for(
            _drive(
                graph,
                {
                    "question": question,
                    "project_key": project_key.upper(),
                    "max_probe_steps": budgets.max_probe_steps,
                    "probe_count": 0,
                },
                {"configurable": {"ctx": ctx}},
                on_event,
            ),
            timeout=budgets.deadline_s,
        )
    except TimeoutError as exc:
        raise AskDeadlineExceeded(budgets.deadline_s) from exc
    result = AgentResult(
        answer=str(final.get("answer") or ""),
        citations=list(final.get("citations") or []),
        gaps=list(final.get("gaps") or []),
        tool_trail=list(final.get("trail") or []),
        plan=list(final.get("plan") or []),
        conflicts=list(final.get("conflicts") or []),
        rejected_citations=list(final.get("rejected_citations") or []),
        usage=dict(final.get("usage") or {}),
        run_id=run_id,
        checklist=dict(final.get("checklist") or {}),
        hops=list(final.get("hops") or []),
        evidence=_evidence_summary(final.get("pack") or {}),
    )
    if stm is not None and run_id is not None:
        await stm.complete(
            run_id,
            tenant_id=tools.principal.tenant_id,
            answer=result.answer,
            citations=result.citations,
            gaps=result.gaps,
            conflicts=result.conflicts,
            plan=result.plan,
            checklist=result.checklist,
            tool_trail=result.tool_trail,
            usage=result.usage,
        )
    return result
