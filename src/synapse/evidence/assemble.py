"""Assemble an EvidencePack from tool results and detect live-vs-RAG conflicts."""

from __future__ import annotations

from typing import Any

from synapse.evidence.types import ConflictNote, EvidenceItem, EvidencePack, SourceKind


def assemble_pack(
    *,
    project_key: str,
    tool_results: list[dict[str, Any]],
) -> EvidencePack:
    live_status: str | None = None
    items: list[EvidenceItem] = []
    gaps: list[str] = []

    for entry in tool_results:
        tool = entry.get("tool") or ""
        result = entry.get("result") or {}
        if result.get("error"):
            gaps.append(f"{tool}:{result['error']}")
            continue

        if tool == "project_lookup":
            if not result.get("found"):
                gaps.append("project_not_found")
                continue
            live_status = str(result.get("status") or "")
            items.append(
                EvidenceItem(
                    source_kind=SourceKind.LIVE,
                    source="project_lookup",
                    record_id=str(result["id"]),
                    summary=(
                        f"{result.get('key')} status={live_status} "
                        f"priority={result.get('priority')}"
                    ),
                    payload=result,
                    precedence=0,
                )
            )

        elif tool == "risk_list":
            for r in result.get("risks") or []:
                items.append(
                    EvidenceItem(
                        source_kind=SourceKind.LIVE,
                        source="risk_list",
                        record_id=str(r["id"]),
                        summary=f"risk {r.get('severity')}/{r.get('status')}: {r.get('title')}",
                        payload=r,
                        precedence=0,
                    )
                )

        elif tool == "blocker_list":
            for b in result.get("blockers") or []:
                items.append(
                    EvidenceItem(
                        source_kind=SourceKind.LIVE,
                        source="blocker_list",
                        record_id=str(b["id"]),
                        summary=f"blocker {b.get('status')}: {b.get('title')}",
                        payload=b,
                        precedence=0,
                    )
                )

        elif tool == "task_search":
            for t in result.get("tasks") or []:
                items.append(
                    EvidenceItem(
                        source_kind=SourceKind.LIVE,
                        source="task_search",
                        record_id=str(t["id"]),
                        summary=f"task {t.get('key')} {t.get('status')}: {t.get('title')}",
                        payload=t,
                        precedence=0,
                    )
                )

        elif tool == "project_activity":
            for e in result.get("events") or []:
                items.append(
                    EvidenceItem(
                        source_kind=SourceKind.LIVE,
                        source="project_activity",
                        record_id=str(e["id"]),
                        summary=str(e.get("summary") or e.get("event_type")),
                        payload=e,
                        precedence=0,
                    )
                )

        elif tool == "document_search":
            hits = result.get("hits") or []
            if not hits:
                gaps.append("no_document_hits")
            for h in hits:
                stale = bool(h.get("stale_vs_live") or h.get("contradicts_live_status"))
                items.append(
                    EvidenceItem(
                        source_kind=SourceKind.RAG,
                        source="document_search",
                        record_id=str(h.get("chunk_id") or h.get("document_id")),
                        summary=(h.get("text") or "")[:240],
                        payload=h,
                        framed=h.get("framed"),
                        stale_vs_live=stale,
                        precedence=10,
                    )
                )

        elif tool == "email_search":
            for e in result.get("emails") or []:
                items.append(
                    EvidenceItem(
                        source_kind=SourceKind.LIVE,
                        source="email_search",
                        record_id=str(e["id"]),
                        summary=f"email: {e.get('subject')} — {str(e.get('body') or '')[:160]}",
                        payload=e,
                        precedence=0,
                    )
                )

        elif tool == "meeting_search":
            for m in result.get("meetings") or []:
                items.append(
                    EvidenceItem(
                        source_kind=SourceKind.LIVE,
                        source="meeting_search",
                        record_id=str(m["id"]),
                        summary=f"meeting: {m.get('title')} — {str(m.get('notes') or '')[:160]}",
                        payload=m,
                        precedence=0,
                    )
                )

        elif tool in {
            "hn_search",
            "stackoverflow_search",
            "tavily_search",
        }:
            if result.get("unavailable"):
                gaps.append(f"{tool}:unavailable")
                continue
            hits = result.get("hits") or []
            if not hits:
                gaps.append(f"{tool}:empty")
            for h in hits:
                items.append(
                    EvidenceItem(
                        source_kind=SourceKind.EXTERNAL,
                        source=tool,
                        record_id=str(h.get("id")),
                        summary=(h.get("text") or h.get("title") or "")[:240],
                        payload=h,
                        framed=h.get("framed"),
                        precedence=5,
                    )
                )

        elif tool == "memory_search":
            memories = result.get("memories") or []
            if not memories:
                gaps.append("no_ltm_hits")
            for m in memories:
                items.append(
                    EvidenceItem(
                        source_kind=SourceKind.LTM,
                        source="memory_search",
                        record_id=str(m["id"]),
                        summary=str(m.get("content") or "")[:240],
                        payload=m,
                        precedence=20,
                    )
                )

    conflicts = detect_conflicts(live_status=live_status, items=items)
    return EvidencePack(
        project_key=project_key,
        live_status=live_status,
        items=items,
        conflicts=conflicts,
        gaps=gaps,
    )


def detect_conflicts(*, live_status: str | None, items: list[EvidenceItem]) -> list[ConflictNote]:
    if not live_status:
        return []
    troubled = live_status in {"at_risk", "delayed", "cancelled"}
    if not troubled:
        return []
    notes: list[ConflictNote] = []
    for item in items:
        if item.source_kind != SourceKind.RAG:
            continue
        text = (item.summary or "").lower()
        optimistic = any(
            phrase in text for phrase in ("on track", "on-track", "green", "no risks", "healthy")
        )
        if item.stale_vs_live or optimistic:
            notes.append(
                ConflictNote(
                    document_id=str(item.payload.get("document_id") or item.record_id),
                    live_status=live_status,
                    detail=(
                        f"Document reads optimistic while live project status is '{live_status}'. "
                        "Prefer live status; treat the document as stale."
                    ),
                )
            )
    return notes
