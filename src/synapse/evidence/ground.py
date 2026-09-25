"""Ground model citations against the EvidencePack — drop invented ids."""

from __future__ import annotations

from typing import Any

from synapse.evidence.types import Citation, EvidencePack, SourceKind


def ground_citations(
    claimed: list[dict[str, Any]], pack: EvidencePack
) -> tuple[list[Citation], list[str]]:
    """Return (verified citations, rejected ids)."""
    allowed = pack.index()
    verified: list[Citation] = []
    rejected: list[str] = []
    seen: set[str] = set()

    for raw in claimed:
        rid = str(raw.get("id") or "").strip()
        if not rid:
            continue
        if rid in seen:
            continue
        item = allowed.get(rid)
        if item is None:
            rejected.append(rid)
            continue
        seen.add(rid)
        verified.append(
            Citation(
                source_kind=item.source_kind,
                source=str(raw.get("source") or item.source),
                record_id=rid,
                note=str(raw.get("note") or ""),
            )
        )

    # Always surface conflict documents as citations when present.
    for conflict in pack.conflicts:
        # Prefer chunk id if in pack, else document id note.
        for item in pack.items:
            if item.source_kind == SourceKind.RAG and (
                item.payload.get("document_id") == conflict.document_id
                or item.record_id == conflict.document_id
            ):
                if item.record_id not in seen:
                    verified.append(
                        Citation(
                            source_kind=SourceKind.RAG,
                            source="document_search",
                            record_id=item.record_id,
                            note="stale_vs_live",
                        )
                    )
                    seen.add(item.record_id)
                break

    return verified, rejected
