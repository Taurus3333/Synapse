"""Agent entrypoint — LangGraph-backed bounded orchestration."""

from __future__ import annotations

import os

from openai import AsyncOpenAI

from synapse.agent.graph import AgentBudgets, AgentResult, GraphContext, get_graph
from synapse.memory.stm import ShortTermMemory
from synapse.tools.runtime import ToolSession

__all__ = ["AgentBudgets", "AgentResult", "run_agent"]


def _client() -> tuple[AsyncOpenAI, str]:
    groq = os.environ.get("SYNAPSE_GROQ_API_KEY") or os.environ.get("GROQ_API_KEY")
    openai_key = os.environ.get("SYNAPSE_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if groq:
        return (
            AsyncOpenAI(api_key=groq, base_url="https://api.groq.com/openai/v1"),
            "openai/gpt-oss-20b",
        )
    if openai_key:
        return AsyncOpenAI(api_key=openai_key), "gpt-4o-mini"
    raise RuntimeError("Set SYNAPSE_GROQ_API_KEY or SYNAPSE_OPENAI_API_KEY")


async def run_agent(
    question: str,
    tools: ToolSession,
    *,
    project_key: str = "ATLAS",
    budgets: AgentBudgets | None = None,
    stm: ShortTermMemory | None = None,
    run_id: str | None = None,
) -> AgentResult:
    budgets = budgets or AgentBudgets()
    client, model = _client()
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
    final = await graph.ainvoke(
        {
            "question": question,
            "project_key": project_key.upper(),
            "max_probe_steps": budgets.max_probe_steps,
            "probe_count": 0,
        },
        config={"configurable": {"ctx": ctx}},
    )
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
