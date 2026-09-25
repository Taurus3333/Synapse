"""Golden evaluation cases — expectations tied to seed-42 corpus ids.

Cases encode *what must be true of retrieval*, not prose quality.
Answer checks are optional fixtures for offline scoring; live ask is not required
for the CI gate (multihop pack scoring is deterministic without a chat model).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class GoldenCase(BaseModel):
    """One regression question with closed-world expectations."""

    id: str
    question: str
    project_key: str = "ATLAS"
    seed: int = 42
    profile: str = "ci"
    mode: Literal["multihop"] = "multihop"
    expect_live_status: str | None = None
    require_pack_ids: list[str] = Field(default_factory=list)
    require_hop_tools: list[str] = Field(default_factory=list)
    require_sources: list[str] = Field(default_factory=list)
    # Offline answer scoring (fixtures / full ask when measured later)
    require_cited_ids: list[str] = Field(default_factory=list)
    require_answer_substrings: list[str] = Field(default_factory=list)
    forbid_answer_substrings: list[str] = Field(default_factory=list)
    max_rejected_citations: int | None = None
    notes: str = ""


def atlas_q2_case() -> GoldenCase:
    """Canonical portfolio question — ATLAS seed-42."""
    return GoldenCase(
        id="atlas_q2_risks",
        question=(
            "Summarize what changed in Project Atlas during Q2 and identify the major risks."
        ),
        project_key="ATLAS",
        expect_live_status="at_risk",
        require_pack_ids=[
            "prj_nw_00001",
            "rsk_nw_00003",
            "blk_nw_00002",
            "eml_nw_00007",
            "mtg_nw_00003",
        ],
        require_hop_tools=[
            "document_search",
            "email_search",
            "meeting_search",
            "memory_search",
        ],
        require_sources=["live", "email_or_meeting"],
        require_cited_ids=["rsk_nw_00003", "prj_nw_00001"],
        require_answer_substrings=["at_risk", "SDK"],
        forbid_answer_substrings=["on track with no major risks", "everything is fine"],
        max_rejected_citations=0,
        notes=(
            "Live status at_risk; Harbor SDK risk/blocker; follow must surface "
            "eml_nw_00007 + mtg_nw_00003. Seed 42 / profile ci."
        ),
    )


def harbor_status_case() -> GoldenCase:
    """Second project — delayed live status + open blocker."""
    return GoldenCase(
        id="harbor_delayed",
        question="What is Harbor's delivery status and what is blocking it?",
        project_key="HARBOR",
        expect_live_status="delayed",
        require_pack_ids=["prj_nw_00002", "blk_nw_00003"],
        require_hop_tools=["document_search", "email_search", "meeting_search"],
        require_sources=["live"],
        require_cited_ids=["prj_nw_00002", "blk_nw_00003"],
        require_answer_substrings=["delayed"],
        forbid_answer_substrings=["on track", "no blockers"],
        max_rejected_citations=0,
        notes="HARBOR seed-42 is delayed with open cache-invalidation blocker.",
    )


def default_suite() -> list[GoldenCase]:
    return [atlas_q2_case(), harbor_status_case()]


def load_suite(case_ids: list[str] | None = None) -> list[GoldenCase]:
    suite = {c.id: c for c in default_suite()}
    if not case_ids:
        return list(suite.values())
    missing = [i for i in case_ids if i not in suite]
    if missing:
        raise KeyError(f"unknown golden case ids: {missing}")
    return [suite[i] for i in case_ids]
