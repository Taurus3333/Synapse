"""Typed evidence for citations. Live facts outrank RAG text."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class SourceKind(StrEnum):
    LIVE = "live"  # enterprise Postgres — highest trust
    EXTERNAL = "external"  # Hacker News, Stack Overflow, Tavily — fetched at ask time
    RAG = "rag"
    LTM = "ltm"  # durable memory — never overrides live status


@dataclass(frozen=True)
class Citation:
    source_kind: SourceKind
    source: str  # tool or document
    record_id: str
    note: str = ""


@dataclass
class EvidenceItem:
    source_kind: SourceKind
    source: str
    record_id: str
    summary: str
    payload: dict[str, Any] = field(default_factory=dict)
    framed: str | None = None
    stale_vs_live: bool = False
    precedence: int = 0  # lower = higher trust; live=0, rag=10


@dataclass
class ConflictNote:
    """RAG/document claim that disagrees with live project state."""

    document_id: str
    live_status: str
    detail: str


@dataclass
class EvidencePack:
    project_key: str
    live_status: str | None
    items: list[EvidenceItem] = field(default_factory=list)
    conflicts: list[ConflictNote] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)

    def index(self) -> dict[str, EvidenceItem]:
        return {i.record_id: i for i in self.items}

    def allowed_citation_ids(self) -> set[str]:
        return {i.record_id for i in self.items}

    def to_prompt(self) -> dict[str, Any]:
        """Compact pack for the synthesizer. RAG text is pre-framed as DATA."""

        def _trim(text: str | None, n: int = 280) -> str:
            s = (text or "").strip()
            return s if len(s) <= n else s[: n - 1] + "…"

        def _live_payload(payload: dict[str, Any]) -> dict[str, Any]:
            keep = (
                "status",
                "severity",
                "title",
                "key",
                "priority",
                "subject",
                "name",
            )
            return {k: payload.get(k) for k in keep if payload.get(k) is not None}

        live = [i for i in self.items if i.source_kind == SourceKind.LIVE][:24]
        rag = [i for i in self.items if i.source_kind == SourceKind.RAG][:6]
        external = [i for i in self.items if i.source_kind == SourceKind.EXTERNAL][:6]
        ltm = [i for i in self.items if i.source_kind == SourceKind.LTM][:6]
        return {
            "project_key": self.project_key,
            "live_status": self.live_status,
            "precedence": (
                "live > external APIs > rag > ltm; never let LTM override live project status"
            ),
            "live_evidence": [
                {
                    "id": i.record_id,
                    "source": i.source,
                    "summary": _trim(i.summary, 220),
                    "fields": _live_payload(i.payload),
                }
                for i in live
            ],
            "external_evidence": [
                {
                    "id": i.record_id,
                    "source": i.source,
                    "summary": _trim(i.summary or i.framed, 220),
                }
                for i in external
            ],
            "rag_evidence": [
                {
                    "id": i.record_id,
                    "document_id": i.payload.get("document_id"),
                    "summary": _trim(i.summary, 220),
                    "stale_vs_live": i.stale_vs_live,
                }
                for i in rag
            ],
            "ltm_evidence": [
                {
                    "id": i.record_id,
                    "source": i.source,
                    "summary": _trim(i.summary, 220),
                    "kind": i.payload.get("kind"),
                }
                for i in ltm
            ],
            "conflicts": [
                {
                    "document_id": c.document_id,
                    "live_status": c.live_status,
                    "detail": _trim(c.detail, 180),
                }
                for c in self.conflicts
            ],
            "gaps": self.gaps,
        }
