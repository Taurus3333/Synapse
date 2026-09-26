"""Public execution events. Node names and counts only — not model text."""

from __future__ import annotations

from typing import Any

_LABELS = {
    "plan": "Plan created",
    "gather": "Live data loaded",
    "follow": "Multi-hop step",
    "probe": "Additional retrieval",
    "finish": "Bounded finish",
    "evidence": "Evidence collected",
    "synthesise": "Answer synthesis and citation check",
}


def _tool_rows(rows: object) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = row.get("tool")
        if not name:
            continue
        item: dict[str, Any] = {"tool": str(name)}
        if "ok" in row:
            item["ok"] = bool(row.get("ok"))
        if row.get("error"):
            item["error"] = str(row.get("error"))[:120]
        out.append(item)
    return out


def public_events(node: str, delta: dict[str, Any]) -> list[dict[str, Any]]:
    """One structured event for a finished graph node. Drops answer text and prompts."""
    if node not in _LABELS or not isinstance(delta, dict):
        return []
    detail: dict[str, Any] = {}
    if node == "plan":
        detail["slots"] = [str(slot) for slot in (delta.get("plan") or [])]
    elif node == "gather":
        detail["tools"] = _tool_rows(delta.get("trail"))
    elif node == "follow":
        detail["hops"] = _tool_rows(delta.get("hops"))
    elif node == "probe":
        detail["probe_count"] = delta.get("probe_count")
        detail["tools"] = _tool_rows(delta.get("trail"))
    elif node == "finish":
        detail["tools"] = _tool_rows(delta.get("trail"))
    elif node == "evidence":
        raw_pack = delta.get("pack")
        pack: dict[str, Any] = raw_pack if isinstance(raw_pack, dict) else {}
        detail["item_count"] = pack.get("item_count")
        detail["gap_count"] = len(pack.get("gaps") or [])
        detail["conflict_count"] = len(pack.get("conflicts") or [])
    else:
        detail["citation_count"] = len(delta.get("citations") or [])
        detail["rejected_count"] = len(delta.get("rejected_citations") or [])
        detail["gap_count"] = len(delta.get("gaps") or [])
    return [{"step": node, "label": _LABELS[node], "detail": detail}]
