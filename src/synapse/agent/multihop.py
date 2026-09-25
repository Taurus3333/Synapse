"""Deterministic multi-hop follow-ups from live evidence.

After gather, code inspects risks/blockers/tasks and queues the next
retrieval hops (docs, memory, email, meetings, github). The LLM does not
invent these first hops — that is the Chunk 11 integration contract.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_STOP = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "from",
        "that",
        "this",
        "into",
        "over",
        "under",
        "open",
        "risk",
        "blocker",
        "task",
        "status",
        "project",
        "atlas",
        "harbor",
        "quay",
        "beacon",
    }
)


@dataclass(frozen=True)
class FollowHop:
    tool: str
    args: dict[str, Any]
    reason: str
    source_ids: tuple[str, ...]


def _tokens(text: str) -> list[str]:
    words = re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", text.lower())
    out: list[str] = []
    for w in words:
        if w in _STOP:
            continue
        if w not in out:
            out.append(w)
        if len(out) >= 6:
            break
    return out


def _query_from_texts(texts: list[str], *, fallback: str) -> str:
    # Prefer concrete delivery nouns over long title dumps (web search is brittle).
    preferred = (
        "sdk",
        "vendor",
        "freeze",
        "blocker",
        "cutover",
        "delay",
        "outage",
        "risk",
        "integration",
    )
    bag: list[str] = []
    lowered_corpus = " ".join(texts).lower()
    for pref in preferred:
        if pref in lowered_corpus and pref not in bag:
            bag.append(pref)
        if len(bag) >= 4:
            break
    if bag:
        return " ".join(bag)
    for t in texts:
        for tok in _tokens(t):
            if tok not in bag:
                bag.append(tok)
            if len(bag) >= 4:
                break
    return " ".join(bag) if bag else fallback


def plan_follow_hops(
    *,
    project_key: str,
    question: str,
    tool_results: list[dict[str, Any]],
    max_hops: int = 10,
) -> list[FollowHop]:
    """Derive code-owned follow-up tool calls from gather results."""
    risk_titles: list[str] = []
    risk_ids: list[str] = []
    blocker_titles: list[str] = []
    blocker_ids: list[str] = []
    task_titles: list[str] = []
    live_status: str | None = None

    for entry in tool_results:
        tool = entry.get("tool")
        result = entry.get("result") or {}
        if tool == "project_lookup" and result.get("found"):
            live_status = str(result.get("status") or "")
        elif tool == "risk_list":
            for r in result.get("risks") or []:
                if str(r.get("status") or "").lower() in {"open", "mitigating"}:
                    risk_titles.append(str(r.get("title") or ""))
                    risk_ids.append(str(r.get("id") or ""))
        elif tool == "blocker_list":
            for b in result.get("blockers") or []:
                if str(b.get("status") or "").lower() == "open":
                    blocker_titles.append(str(b.get("title") or ""))
                    blocker_ids.append(str(b.get("id") or ""))
        elif tool == "task_search":
            for t in result.get("tasks") or []:
                st = str(t.get("status") or "").lower()
                if st in {"blocked", "in_progress", "todo"}:
                    task_titles.append(str(t.get("title") or ""))

    signals = [t for t in risk_titles + blocker_titles if t]
    query = _query_from_texts(signals or task_titles, fallback=question[:120])
    src = tuple(i for i in risk_ids + blocker_ids if i)[:8]

    hops: list[FollowHop] = []

    def add(tool: str, args: dict[str, Any], reason: str) -> None:
        if len(hops) >= max_hops:
            return
        hops.append(FollowHop(tool=tool, args=args, reason=reason, source_ids=src))

    if signals or live_status in {"at_risk", "delayed"}:
        add(
            "document_search",
            {"project_key": project_key, "query": query, "limit": 5},
            "live risks/blockers imply document narrative hop",
        )
        add(
            "email_search",
            {"project_key": project_key, "query": query, "limit": 5},
            "cross-source hop into project email",
        )
        add(
            "meeting_search",
            {"project_key": project_key, "query": query, "limit": 5},
            "cross-source hop into meeting notes",
        )
        add(
            "memory_search",
            {"project_key": project_key, "query": query, "limit": 5},
            "recall prior LTM notes for same signals",
        )
        add(
            "github_search",
            {"query": query, "limit": 3},
            "live public GitHub hop (default kubernetes/kubernetes)",
        )
        add(
            "hn_search",
            {"query": query, "limit": 3},
            "live public Hacker News hop",
        )
        add(
            "stackoverflow_search",
            {"query": query, "limit": 3},
            "live public Stack Overflow hop",
        )
        add(
            "wikipedia_search",
            {"query": query, "limit": 3},
            "live public Wikipedia hop",
        )
    else:
        add(
            "document_search",
            {"project_key": project_key, "query": question[:160], "limit": 5},
            "baseline doc hop from question",
        )
        add(
            "memory_search",
            {"project_key": project_key, "query": question[:160], "limit": 5},
            "baseline memory hop from question",
        )

    return hops[:max_hops]
