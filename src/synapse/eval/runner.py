"""Run golden cases against the LLM-free multihop path."""

from __future__ import annotations

from typing import Any

from synapse.agent.integrate import run_multihop_integration
from synapse.eval.cases import GoldenCase, load_suite
from synapse.eval.score import CaseScore, SuiteReport, score_answer, score_multihop
from synapse.tools.runtime import ToolSession


async def run_case_multihop(tools: ToolSession, case: GoldenCase) -> CaseScore:
    result = await run_multihop_integration(
        tools,
        project_key=case.project_key,
        question=case.question,
    )
    pack = result.pack
    return score_multihop(
        case,
        live_status=pack.live_status if pack else None,
        pack_items=list(pack.items) if pack else [],
        hops=result.hops,
        sources_present=result.sources_present,
    )


async def run_suite(
    tools: ToolSession,
    case_ids: list[str] | None = None,
) -> SuiteReport:
    cases = load_suite(case_ids)
    scores: list[CaseScore] = []
    for case in cases:
        scores.append(await run_case_multihop(tools, case))
    return SuiteReport(scores=scores)


def score_answer_fixture(
    case: GoldenCase,
    *,
    answer: str,
    citations: list[dict[str, Any]],
    rejected_citations: list[str] | None = None,
) -> CaseScore:
    """Offline answer scoring for fixtures (no live LLM)."""
    return score_answer(
        case,
        answer=answer,
        citations=citations,
        rejected_citations=rejected_citations,
    )
