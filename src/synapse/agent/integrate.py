"""LLM-free multi-hop integration path for verification and demos.

Runs gather + follow + evidence assembly without calling a chat model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from synapse.agent.graph import AgentState, GraphContext, node_follow, node_gather
from synapse.agent.multihop import plan_follow_hops
from synapse.evidence.assemble import assemble_pack
from synapse.evidence.types import EvidencePack, SourceKind
from synapse.tools.runtime import ToolSession


@dataclass
class MultiHopResult:
    project_key: str
    hops: list[dict[str, Any]] = field(default_factory=list)
    tool_trail: list[dict[str, Any]] = field(default_factory=list)
    pack: EvidencePack | None = None
    sources_present: dict[str, bool] = field(default_factory=dict)
    external_providers: list[str] = field(default_factory=list)


class _NoopClient:
    """Stand-in so GraphContext type-checks; gather/follow never call chat."""


async def run_multihop_integration(
    tools: ToolSession,
    *,
    project_key: str = "ATLAS",
    question: str = "Summarize Q2 changes and major risks",
) -> MultiHopResult:
    """Deterministic gather→follow→pack. Used by tests and /v1/multihop."""
    ctx = GraphContext(tools=tools, client=_NoopClient(), model="none")  # type: ignore[arg-type]
    state: AgentState = {
        "question": question,
        "project_key": project_key.upper(),
        "plan": [],
        "checklist": {
            "project_baseline": "empty",
            "risk_records": "empty",
            "open_blockers": "empty",
            "activity_in_window": "empty",
            "slipped_work": "empty",
            "supporting_documents": "empty",
            "cross_source_follow": "empty",
            "prior_memory": "empty",
            "external_signals": "empty",
        },
        "raw_evidence": [],
        "trail": [],
        "fingerprints": [],
        "hops": [],
    }
    config = {"configurable": {"ctx": ctx}}
    gathered = await node_gather(state, config)  # type: ignore[arg-type]
    state.update(gathered)
    followed = await node_follow(state, config)  # type: ignore[arg-type]
    state.update(followed)
    pack = assemble_pack(
        project_key=project_key.upper(),
        tool_results=list(state.get("raw_evidence") or []),
    )
    kinds = {i.source_kind for i in pack.items}
    external_items = [i for i in pack.items if i.source_kind == SourceKind.EXTERNAL]
    providers = sorted(
        {
            str((i.payload or {}).get("provider") or i.source)
            for i in external_items
        }
    )
    return MultiHopResult(
        project_key=project_key.upper(),
        hops=list(state.get("hops") or []),
        tool_trail=list(state.get("trail") or []),
        pack=pack,
        sources_present={
            "live": SourceKind.LIVE in kinds,
            "rag": SourceKind.RAG in kinds,
            "external": SourceKind.EXTERNAL in kinds,
            "ltm": SourceKind.LTM in kinds,
            "email_or_meeting": any(
                i.source in {"email_search", "meeting_search"} for i in pack.items
            ),
            "conflicts": bool(pack.conflicts),
        },
        external_providers=providers,
    )


def explain_atlas_hops(tool_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pure helper: show planned hops for ATLAS-like gather output."""
    hops = plan_follow_hops(
        project_key="ATLAS",
        question="Q2 risks",
        tool_results=tool_results,
    )
    return [
        {"tool": h.tool, "args": h.args, "reason": h.reason, "source_ids": list(h.source_ids)}
        for h in hops
    ]
