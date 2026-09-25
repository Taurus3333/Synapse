"""Deterministic eval scorers — no LLM-as-judge.

Metrics are set membership and string checks against known seed ids.
Pass/fail is boolean per check; suite pass rate is the only aggregate score.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from synapse.eval.cases import GoldenCase


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class CaseScore:
    case_id: str
    checks: list[CheckResult] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "passed": self.passed,
            "metrics": dict(self.metrics),
            "checks": [
                {"name": c.name, "passed": c.passed, "detail": c.detail} for c in self.checks
            ],
        }


@dataclass
class SuiteReport:
    scores: list[CaseScore] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return bool(self.scores) and all(s.passed for s in self.scores)

    @property
    def pass_rate(self) -> float:
        if not self.scores:
            return 0.0
        return sum(1 for s in self.scores if s.passed) / len(self.scores)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "pass_rate": round(self.pass_rate, 4),
            "n_cases": len(self.scores),
            "n_passed": sum(1 for s in self.scores if s.passed),
            "cases": [s.to_dict() for s in self.scores],
        }


def _pack_ids(pack_items: list[Any]) -> set[str]:
    ids: set[str] = set()
    for item in pack_items:
        rid = getattr(item, "record_id", None)
        if rid is None and isinstance(item, dict):
            rid = item.get("record_id") or item.get("id")
        if rid:
            ids.add(str(rid))
    return ids


def score_multihop(
    case: GoldenCase,
    *,
    live_status: str | None,
    pack_items: list[Any],
    hops: list[dict[str, Any]],
    sources_present: dict[str, bool],
) -> CaseScore:
    """Score gather→follow→pack against golden expectations."""
    checks: list[CheckResult] = []
    metrics: dict[str, float] = {}

    if case.expect_live_status is not None:
        ok = (live_status or "") == case.expect_live_status
        checks.append(
            CheckResult(
                "live_status",
                ok,
                f"got={live_status!r} want={case.expect_live_status!r}",
            )
        )
        metrics["live_status_match"] = 1.0 if ok else 0.0

    found = _pack_ids(pack_items)
    required = list(case.require_pack_ids)
    if required:
        hit = [rid for rid in required if rid in found]
        miss = [rid for rid in required if rid not in found]
        recall = len(hit) / len(required)
        metrics["pack_id_recall"] = recall
        checks.append(
            CheckResult(
                "require_pack_ids",
                len(miss) == 0,
                f"hit={hit} miss={miss} pack_size={len(found)}",
            )
        )

    hop_tools = {str(h.get("tool") or "") for h in hops}
    if case.require_hop_tools:
        miss_hops = [t for t in case.require_hop_tools if t not in hop_tools]
        metrics["hop_tool_recall"] = (
            (len(case.require_hop_tools) - len(miss_hops)) / len(case.require_hop_tools)
        )
        checks.append(
            CheckResult(
                "require_hop_tools",
                len(miss_hops) == 0,
                f"got={sorted(hop_tools)} miss={miss_hops}",
            )
        )

    if case.require_sources:
        miss_src = [s for s in case.require_sources if not sources_present.get(s)]
        checks.append(
            CheckResult(
                "require_sources",
                len(miss_src) == 0,
                f"sources={sources_present} miss={miss_src}",
            )
        )

    if not checks:
        checks.append(CheckResult("empty_expectations", False, "case has no checks"))

    return CaseScore(case_id=case.id, checks=checks, metrics=metrics)


def score_answer(
    case: GoldenCase,
    *,
    answer: str,
    citations: list[dict[str, Any]],
    rejected_citations: list[str] | None = None,
) -> CaseScore:
    """Score a synthesised answer (fixture or live ask) with closed-world rules."""
    checks: list[CheckResult] = []
    metrics: dict[str, float] = {}
    text = answer or ""
    cited_ids = {
        str(c.get("id") or c.get("record_id") or "")
        for c in citations
        if (c.get("id") or c.get("record_id"))
    }
    rejected = list(rejected_citations or [])

    if case.require_cited_ids:
        miss = [rid for rid in case.require_cited_ids if rid not in cited_ids]
        metrics["citation_id_recall"] = (
            (len(case.require_cited_ids) - len(miss)) / len(case.require_cited_ids)
        )
        checks.append(
            CheckResult(
                "require_cited_ids",
                len(miss) == 0,
                f"cited={sorted(cited_ids)} miss={miss}",
            )
        )

    if case.require_answer_substrings:
        miss_sub = [s for s in case.require_answer_substrings if s.lower() not in text.lower()]
        checks.append(
            CheckResult(
                "require_answer_substrings",
                len(miss_sub) == 0,
                f"miss={miss_sub}",
            )
        )

    if case.forbid_answer_substrings:
        hits = [s for s in case.forbid_answer_substrings if s.lower() in text.lower()]
        checks.append(
            CheckResult(
                "forbid_answer_substrings",
                len(hits) == 0,
                f"forbidden_hits={hits}",
            )
        )

    if case.max_rejected_citations is not None:
        ok = len(rejected) <= case.max_rejected_citations
        metrics["rejected_citations"] = float(len(rejected))
        checks.append(
            CheckResult(
                "max_rejected_citations",
                ok,
                f"rejected={rejected} max={case.max_rejected_citations}",
            )
        )

    claimed = len(citations) + len(rejected)
    if claimed:
        metrics["citation_precision"] = len(citations) / claimed
    else:
        metrics["citation_precision"] = 1.0 if not rejected else 0.0

    if not checks:
        checks.append(CheckResult("empty_answer_expectations", False, "no answer checks"))

    return CaseScore(case_id=case.id, checks=checks, metrics=metrics)


def merge_scores(*scores: CaseScore) -> CaseScore:
    """Combine multihop + answer scores for one case id."""
    if not scores:
        raise ValueError("no scores")
    case_id = scores[0].case_id
    checks: list[CheckResult] = []
    metrics: dict[str, float] = {}
    for s in scores:
        if s.case_id != case_id:
            raise ValueError(f"case_id mismatch {case_id} vs {s.case_id}")
        checks.extend(s.checks)
        metrics.update(s.metrics)
    return CaseScore(case_id=case_id, checks=checks, metrics=metrics)
