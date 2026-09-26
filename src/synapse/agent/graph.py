"""LangGraph orchestration: plan → gather → follow → probe* → finish → evidence → synthesise.

The graph is the workflow skeleton. Budgets, tools, grounding, and STM stay in code.
Follow hops are code-owned (live risks/blockers → docs/email/meetings/memory/HN/SO/Tavily).
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, Literal, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from openai import AsyncOpenAI

from synapse.agent.multihop import plan_follow_hops
from synapse.evidence.assemble import assemble_pack
from synapse.evidence.ground import ground_citations
from synapse.guardrails.allowlists import PLAN_SLOTS
from synapse.guardrails.structured import (
    parse_plan,
    parse_probe,
    parse_synthesis,
    validate_answer_safety,
)
from synapse.memory.stm import ShortTermMemory
from synapse.perf.usage import UsageAccumulator
from synapse.reliability.circuit import CircuitBreaker, CircuitOpen, get_chat_circuit
from synapse.tools.runtime import ToolSession, call_tool


class AgentState(TypedDict, total=False):
    question: str
    project_key: str
    plan: list[str]
    checklist: dict[str, str]
    raw_evidence: list[dict[str, Any]]
    trail: list[dict[str, Any]]
    fingerprints: list[str]
    hops: list[dict[str, Any]]
    probe_count: int
    max_probe_steps: int
    pack: dict[str, Any]
    answer: str
    citations: list[dict[str, str]]
    gaps: list[str]
    conflicts: list[dict[str, str]]
    rejected_citations: list[str]
    usage: dict[str, Any]


@dataclass
class GraphContext:
    tools: ToolSession
    client: AsyncOpenAI
    model: str
    stm: ShortTermMemory | None = None
    run_id: str | None = None
    tenant_id: str = ""
    usage: UsageAccumulator = field(default_factory=UsageAccumulator)


@dataclass
class AgentBudgets:
    max_probe_steps: int = 3
    max_tool_calls: int = 28
    deadline_s: float = 120.0
    chat_timeout_s: float = 25.0


@dataclass
class AgentResult:
    answer: str
    citations: list[dict[str, str]]
    gaps: list[str]
    tool_trail: list[dict[str, Any]]
    plan: list[str]
    conflicts: list[dict[str, str]] = field(default_factory=list)
    rejected_citations: list[str] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    run_id: str | None = None
    checklist: dict[str, str] = field(default_factory=dict)
    hops: list[dict[str, Any]] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)


async def _chat(
    client: AsyncOpenAI,
    model: str,
    messages: list[dict[str, str]],
    *,
    temperature: float = 0,
    usage: UsageAccumulator | None = None,
    breaker: CircuitBreaker | None = None,
) -> str:
    from synapse.reliability.retry import RetryPolicy, with_retry

    gate = breaker if breaker is not None else get_chat_circuit()
    gate.before_call()

    def _recover_tool_shaped(exc: BaseException) -> str | None:
        """Groq may reject JSON tool-shaped replies as tool_use_failed — recover the payload."""
        err: dict[str, Any] = {}
        raw = getattr(exc, "body", None)
        if isinstance(raw, dict):
            maybe = raw.get("error")
            if isinstance(maybe, dict):
                err = maybe
        if not err:
            resp = getattr(exc, "response", None)
            try:
                payload = resp.json() if resp is not None else None
            except Exception:
                payload = None
            if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
                err = payload["error"]
        failed = err.get("failed_generation") if err else None
        if not isinstance(failed, str) or not failed.strip():
            text = str(exc)
            match = re.search(r"failed_generation['\"]?:\s*['\"](\{.*\})['\"]", text)
            if match:
                failed = match.group(1)
            else:
                return None
        blob = failed.strip()
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            return blob
        if isinstance(data, dict) and "arguments" in data:
            args = data["arguments"]
            if isinstance(args, str):
                return args
            if isinstance(args, dict):
                if "tool" in args:
                    return json.dumps(args)
                tool_name = str(data.get("name") or "").removeprefix("tool_")
                if tool_name:
                    return json.dumps({"tool": tool_name, "args": args})
                return json.dumps(args)
        if isinstance(data, dict) and "tool" in data:
            return blob
        return blob

    async def _once() -> str:
        try:
            resp = await client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
            )
            if usage is not None and getattr(resp, "usage", None) is not None:
                usage.add_chat(
                    prompt=int(resp.usage.prompt_tokens or 0),
                    completion=int(resp.usage.completion_tokens or 0),
                )
            return (resp.choices[0].message.content or "").strip()
        except Exception as exc:
            recovered = _recover_tool_shaped(exc)
            if recovered:
                if usage is not None:
                    usage.add_chat(prompt=0, completion=0)
                return recovered
            raise

    try:
        text = await with_retry(_once, policy=RetryPolicy(attempts=3, base_delay_s=0.3))
    except CircuitOpen:
        raise
    except asyncio.CancelledError:
        gate.abandon()
        raise
    except Exception:
        gate.record_failure()
        raise
    else:
        gate.record_success()
        return text


async def _ckpt(ctx: GraphContext, phase: str, payload: dict[str, Any]) -> None:
    if ctx.stm is None or ctx.run_id is None:
        return
    await ctx.stm.checkpoint(ctx.run_id, tenant_id=ctx.tenant_id, phase=phase, payload=payload)


async def _exec(
    state: AgentState, ctx: GraphContext, name: str, args: dict[str, Any]
) -> dict[str, Any]:
    fps = set(state.get("fingerprints") or [])
    trail = list(state.get("trail") or [])
    raw = list(state.get("raw_evidence") or [])
    fp = sha256(f"{name}:{json.dumps(args, sort_keys=True, default=str)}".encode()).hexdigest()
    if fp in fps:
        return {"error": "duplicate_tool_call_refused", "tool": name}
    fps.add(fp)
    result = await call_tool(ctx.tools, name, args)
    trail.append({"tool": name, "args": args, "ok": "error" not in result})
    raw.append({"tool": name, "result": result})
    state["fingerprints"] = list(fps)
    state["trail"] = trail
    state["raw_evidence"] = raw
    return result


def _ctx(config: RunnableConfig) -> GraphContext:
    return config["configurable"]["ctx"]  # type: ignore[index]


async def node_plan(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    ctx = _ctx(config)
    plan_raw = await _chat(
        ctx.client,
        ctx.model,
        [
            {
                "role": "system",
                "content": (
                    'Return JSON {"slots":[...]} choosing from: '
                    + ",".join(PLAN_SLOTS)
                    + ". No prose."
                ),
            },
            {"role": "user", "content": state["question"]},
        ],
        usage=ctx.usage,
    )
    parsed, warnings = parse_plan(plan_raw)
    plan = list(parsed.slots)
    trail = [{"tool": "guardrail_plan", "warnings": warnings}] if warnings else []
    await _ckpt(ctx, "plan", {"plan": plan, "guardrail_warnings": warnings})
    return {
        "plan": plan,
        "checklist": {s: "empty" for s in plan},
        "raw_evidence": [],
        "trail": trail,
        "fingerprints": [],
        "hops": [],
        "probe_count": 0,
    }


async def node_gather(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    ctx = _ctx(config)
    key = state["project_key"]
    checklist = dict(state.get("checklist") or {})
    # Mutate via _exec into a working copy of state fields
    work: AgentState = dict(state)
    hop_log: list[dict[str, Any]] = list(work.get("hops") or [])

    async def _live(tool: str, args: dict[str, Any], reason: str) -> dict[str, Any]:
        result = await _exec(work, ctx, tool, args)
        hop_log.append(
            {
                "tool": tool,
                "args": args,
                "reason": reason,
                "source_ids": [],
                "ok": "error" not in result and not result.get("unavailable"),
            }
        )
        return result

    await _live("project_lookup", {"project_key": key}, "live project baseline (status)")
    checklist["project_baseline"] = "filled"
    await _live("risk_list", {"project_key": key}, "live open/mitigating risks")
    checklist["risk_records"] = "filled"
    await _live("blocker_list", {"project_key": key}, "live blockers")
    checklist["open_blockers"] = "filled"
    await _live(
        "project_activity",
        {
            "project_key": key,
            "since": "2026-04-01T00:00:00+00:00",
            "until": "2026-07-01T00:00:00+00:00",
            "limit": 40,
        },
        "live activity in the ask window",
    )
    checklist["activity_in_window"] = "filled"
    await _live("task_search", {"project_key": key}, "live tasks / slipped work")
    checklist["slipped_work"] = "partial"
    await _ckpt(
        ctx,
        "gather",
        {
            "checklist": checklist,
            "tool_trail": work.get("trail"),
            "tool_calls": ctx.tools.call_count,
            "hops": hop_log,
        },
    )
    return {
        "checklist": checklist,
        "raw_evidence": work.get("raw_evidence"),
        "trail": work.get("trail"),
        "fingerprints": work.get("fingerprints"),
        "hops": hop_log,
    }


async def node_follow(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    """Code-owned multi-hop: live findings → docs/email/meetings/memory/web."""
    ctx = _ctx(config)
    key = state["project_key"]
    checklist = dict(state.get("checklist") or {})
    work: AgentState = dict(state)
    planned = plan_follow_hops(
        project_key=key,
        question=state["question"],
        tool_results=list(work.get("raw_evidence") or []),
        max_hops=10,
    )
    hop_log: list[dict[str, Any]] = list(work.get("hops") or [])
    for hop in planned:
        result = await _exec(work, ctx, hop.tool, hop.args)
        hop_log.append(
            {
                "tool": hop.tool,
                "args": hop.args,
                "reason": hop.reason,
                "source_ids": list(hop.source_ids),
                "ok": "error" not in result
                and not result.get("unavailable")
                and bool(
                    result.get("hits")
                    or result.get("emails")
                    or result.get("meetings")
                    or result.get("memories")
                ),
            }
        )
        if hop.tool == "document_search" and result.get("hits"):
            checklist["supporting_documents"] = "filled"
        if hop.tool == "memory_search" and result.get("memories"):
            checklist["prior_memory"] = "filled"
        if hop.tool in {
            "hn_search",
            "stackoverflow_search",
            "tavily_search",
        } and result.get("hits"):
            checklist["external_signals"] = "filled"
        if hop.tool in {"email_search", "meeting_search"} and (
            result.get("emails") or result.get("meetings")
        ):
            checklist["cross_source_follow"] = "filled"
    if checklist.get("cross_source_follow") == "empty" and hop_log:
        checklist["cross_source_follow"] = "partial"
    await _ckpt(
        ctx,
        "follow",
        {"hops": hop_log, "checklist": checklist, "tool_calls": ctx.tools.call_count},
    )
    return {
        "checklist": checklist,
        "raw_evidence": work.get("raw_evidence"),
        "trail": work.get("trail"),
        "fingerprints": work.get("fingerprints"),
        "hops": hop_log,
    }


async def node_probe(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    ctx = _ctx(config)
    key = state["project_key"]
    checklist = dict(state.get("checklist") or {})
    work: AgentState = dict(state)
    missing = [k for k, v in checklist.items() if v == "empty"]
    probe = await _chat(
        ctx.client,
        ctx.model,
        [
            {
                "role": "system",
                "content": (
                    "Pick ONE next tool as JSON "
                    '{"tool":"document_search|email_search|meeting_search|memory_search|'
                    "hn_search|stackoverflow_search|tavily_search|"
                    "task_search|risk_list|"
                    'blocker_list|project_activity|project_lookup",'
                    '"args":{...}}. '
                    f"project_key={key}. Missing slots: {missing}"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "question": state["question"],
                        "evidence_tail": (work.get("raw_evidence") or [])[-2:],
                        "hops": (work.get("hops") or [])[-3:],
                    },
                    default=str,
                ),
            },
        ],
        usage=ctx.usage,
    )
    step, warnings = parse_probe(probe)
    trail = list(work.get("trail") or [])
    if warnings:
        trail.append({"tool": "guardrail_probe", "warnings": warnings})
        work["trail"] = trail
    if step is None:
        return {
            "checklist": checklist,
            "raw_evidence": work.get("raw_evidence"),
            "trail": work.get("trail"),
            "fingerprints": work.get("fingerprints"),
            "probe_count": int(state.get("probe_count") or 0) + 1,
        }
    try:
        name = step.tool
        args = dict(step.args or {})
        if name in {
            "document_search",
            "email_search",
            "meeting_search",
            "task_search",
            "risk_list",
            "blocker_list",
            "project_activity",
            "memory_search",
        }:
            args.setdefault("project_key", key)
        if name in {
            "document_search",
            "email_search",
            "meeting_search",
            "hn_search",
            "stackoverflow_search",
            "tavily_search",
            "memory_search",
        }:
            args.setdefault("query", state["question"])
        result = await _exec(work, ctx, name, args)
        if name == "document_search" and result.get("hits"):
            checklist["supporting_documents"] = "filled"
        if name in {
            "hn_search",
            "stackoverflow_search",
            "tavily_search",
        } and result.get("hits"):
            checklist["external_signals"] = "filled"
        if name == "memory_search" and result.get("memories"):
            checklist["prior_memory"] = "filled"
        if name in {"email_search", "meeting_search"} and (
            result.get("emails") or result.get("meetings")
        ):
            checklist["cross_source_follow"] = "filled"
    except Exception as exc:
        trail = list(work.get("trail") or [])
        trail.append({"tool": "probe", "error": str(exc)})
        work["trail"] = trail
    return {
        "checklist": checklist,
        "raw_evidence": work.get("raw_evidence"),
        "trail": work.get("trail"),
        "fingerprints": work.get("fingerprints"),
        "probe_count": int(state.get("probe_count") or 0) + 1,
    }


def route_after_probe(state: AgentState) -> Literal["probe", "finish"]:
    checklist = state.get("checklist") or {}
    if all(v != "empty" for v in checklist.values()):
        return "finish"
    if int(state.get("probe_count") or 0) >= int(state.get("max_probe_steps") or 3):
        return "finish"
    return "probe"


async def node_finish(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    """Fill remaining empty slots without re-running hops follow already covered."""
    ctx = _ctx(config)
    key = state["project_key"]
    checklist = dict(state.get("checklist") or {})
    work: AgentState = dict(state)
    q = state["question"]
    if checklist.get("supporting_documents") == "empty":
        await _exec(
            work,
            ctx,
            "document_search",
            {"project_key": key, "query": "vendor SDK risk freeze status", "limit": 5},
        )
        checklist["supporting_documents"] = "partial"
    if checklist.get("cross_source_follow") in {"empty", "partial"}:
        em = await _exec(
            work, ctx, "email_search", {"project_key": key, "query": q[:160], "limit": 5}
        )
        mt = await _exec(
            work, ctx, "meeting_search", {"project_key": key, "query": q[:160], "limit": 5}
        )
        if em.get("emails") or mt.get("meetings"):
            checklist["cross_source_follow"] = "filled"
        else:
            checklist["cross_source_follow"] = "partial"
    if checklist.get("prior_memory") == "empty":
        await _exec(
            work, ctx, "memory_search", {"query": q, "project_key": key, "limit": 5}
        )
        checklist["prior_memory"] = "partial"
    if checklist.get("external_signals") == "empty":
        # Public web for the Atlas vendor-SDK freeze. Missing Tavily key is a gap.
        await _exec(
            work, ctx, "hn_search", {"query": "vendor SDK production outage", "limit": 3}
        )
        await _exec(
            work,
            ctx,
            "stackoverflow_search",
            {"query": "SDK deployment failure", "limit": 3},
        )
        await _exec(
            work, ctx, "tavily_search", {"query": "vendor SDK cutover delay", "limit": 3}
        )
        checklist["external_signals"] = "partial"
    await _ckpt(
        ctx,
        "finish",
        {
            "checklist": checklist,
            "tool_trail": work.get("trail"),
            "tool_calls": ctx.tools.call_count,
            "hops": work.get("hops"),
        },
    )
    return {
        "checklist": checklist,
        "raw_evidence": work.get("raw_evidence"),
        "trail": work.get("trail"),
        "fingerprints": work.get("fingerprints"),
    }


async def node_evidence(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    ctx = _ctx(config)
    pack = assemble_pack(
        project_key=state["project_key"], tool_results=list(state.get("raw_evidence") or [])
    )
    await _ckpt(
        ctx,
        "evidence",
        {
            "live_status": pack.live_status,
            "item_count": len(pack.items),
            "conflict_count": len(pack.conflicts),
            "gaps": pack.gaps,
        },
    )
    return {
        "pack": {
            "prompt": pack.to_prompt(),
            "conflicts": [
                {
                    "document_id": c.document_id,
                    "live_status": c.live_status,
                    "detail": c.detail,
                }
                for c in pack.conflicts
            ],
            "gaps": pack.gaps,
            "item_count": len(pack.items),
            "_items_index": {
                i.record_id: {
                    "source": i.source,
                    "kind": str(i.source_kind),
                    "payload": i.payload,
                }
                for i in pack.items
            },
        }
    }


async def node_synthesise(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    ctx = _ctx(config)
    pack_blob = state.get("pack") or {}
    # Rebuild a minimal pack for grounding
    from synapse.evidence.types import ConflictNote, EvidenceItem, EvidencePack, SourceKind

    items = []
    for rid, meta in (pack_blob.get("_items_index") or {}).items():
        items.append(
            EvidenceItem(
                source_kind=SourceKind(meta["kind"]),
                source=meta["source"],
                record_id=rid,
                summary="",
                payload=meta.get("payload") or {},
            )
        )
    conflict_rows = list(pack_blob.get("conflicts") or [])
    pack = EvidencePack(
        project_key=state["project_key"],
        live_status=(pack_blob.get("prompt") or {}).get("live_status"),
        items=items,
        conflicts=[
            ConflictNote(
                document_id=c["document_id"],
                live_status=c["live_status"],
                detail=c["detail"],
            )
            for c in conflict_rows
        ],
        gaps=list(pack_blob.get("gaps") or []),
    )
    conflicts = conflict_rows

    synthesis = await _chat(
        ctx.client,
        ctx.model,
        [
            {
                "role": "system",
                "content": (
                    "You answer ONLY from the evidence pack. "
                    "Precedence: live > external APIs > RAG > LTM. "
                    "Never let LTM override live project status. "
                    "Cite only ids in the pack. "
                    "Return JSON "
                    '{"answer":"...","citations":[{"source":"...","id":"..."}],"gaps":["..."]}'
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "question": state["question"],
                        "checklist": state.get("checklist"),
                        "evidence_pack": pack_blob.get("prompt"),
                        "as_of": datetime.now(UTC).isoformat(),
                    },
                    default=str,
                ),
            },
        ],
        usage=ctx.usage,
    )
    parsed, parse_warnings = parse_synthesis(synthesis)
    claimed = [{"source": c.source, "id": c.id, "note": c.note} for c in parsed.citations]
    verified, rejected = ground_citations(claimed, pack)
    gaps = list(parsed.gaps)
    gaps.extend(pack.gaps)
    gaps.extend(parse_warnings)
    gaps.extend(validate_answer_safety(parsed.answer))
    gaps.extend([f"slot:{k}" for k, v in (state.get("checklist") or {}).items() if v == "empty"])
    if rejected:
        gaps.append(f"ungrounded_citations:{len(rejected)}")
    # de-dupe gaps preserve order
    seen_g: set[str] = set()
    gaps_out: list[str] = []
    for g in gaps:
        if g not in seen_g:
            seen_g.add(g)
            gaps_out.append(g)
    citations = [
        {
            "source": c.source,
            "id": c.record_id,
            "kind": str(c.source_kind),
            **({"note": c.note} if c.note else {}),
        }
        for c in verified
    ]
    from synapse.perf.cost import estimate_run_usd

    usage: dict[str, Any] = {
        "tool_calls": ctx.tools.call_count,
        "evidence_items": int(pack_blob.get("item_count") or 0),
        "probe_steps": int(state.get("probe_count") or 0),
        **ctx.usage.as_dict(),
        "cost_usd": estimate_run_usd(ctx.usage.as_dict()),
    }
    return {
        "answer": parsed.answer,
        "citations": citations,
        "gaps": gaps_out,
        "conflicts": conflicts,
        "rejected_citations": rejected,
        "usage": usage,
    }


def build_agent_graph():
    g = StateGraph(AgentState)
    g.add_node("plan", node_plan)
    g.add_node("gather", node_gather)
    g.add_node("follow", node_follow)
    g.add_node("probe", node_probe)
    g.add_node("finish", node_finish)
    g.add_node("evidence", node_evidence)
    g.add_node("synthesise", node_synthesise)
    g.add_edge(START, "plan")
    g.add_edge("plan", "gather")
    g.add_edge("gather", "follow")
    g.add_edge("follow", "probe")
    g.add_conditional_edges("probe", route_after_probe, {"probe": "probe", "finish": "finish"})
    g.add_edge("finish", "evidence")
    g.add_edge("evidence", "synthesise")
    g.add_edge("synthesise", END)
    return g.compile()


_GRAPH = None


def get_graph():
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = build_agent_graph()
    return _GRAPH


def reset_graph() -> None:
    """Test helper — drop cached compiled graph after structural changes."""
    global _GRAPH
    _GRAPH = None
