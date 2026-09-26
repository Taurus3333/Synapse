"""Allowlists shared by guardrails and the agent graph (cycle-free)."""

from __future__ import annotations

PLAN_SLOTS = [
    "project_baseline",
    "activity_in_window",
    "slipped_work",
    "open_blockers",
    "risk_records",
    "supporting_documents",
    "cross_source_follow",
    "external_signals",
    "prior_memory",
]

ALLOWED_PROBE_TOOLS = frozenset(
    {
        "project_lookup",
        "task_search",
        "risk_list",
        "blocker_list",
        "project_activity",
        "document_search",
        "email_search",
        "meeting_search",
        "hn_search",
        "stackoverflow_search",
        "tavily_search",
        "memory_search",
        "memory_write",
    }
)
