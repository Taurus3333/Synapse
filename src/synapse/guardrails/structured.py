"""Structured output contracts for plan / probe / synthesise."""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator

from synapse.guardrails.allowlists import ALLOWED_PROBE_TOOLS, PLAN_SLOTS

ALLOWED_TOOLS = ALLOWED_PROBE_TOOLS
ALLOWED_SLOTS = frozenset(PLAN_SLOTS)


class PlanOutput(BaseModel):
    slots: list[str] = Field(min_length=1)

    @field_validator("slots")
    @classmethod
    def _known_slots(cls, value: list[str]) -> list[str]:
        cleaned = [s for s in value if s in ALLOWED_SLOTS]
        return cleaned or list(PLAN_SLOTS)


class ProbeStep(BaseModel):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)

    @field_validator("tool")
    @classmethod
    def _known_tool(cls, value: str) -> str:
        if value not in ALLOWED_TOOLS:
            raise ValueError(f"tool not allowlisted: {value}")
        return value


class CitationClaim(BaseModel):
    source: str = ""
    id: str = Field(min_length=1)
    note: str = ""


class SynthesisOutput(BaseModel):
    answer: str = Field(min_length=1)
    citations: list[CitationClaim] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)


def extract_json_object(raw: str) -> dict[str, Any] | None:
    """Pull the first JSON object from model text (fences / prose tolerant)."""
    text = (raw or "").strip()
    if not text:
        return None
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{[\s\S]*\}", text)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def parse_plan(raw: str) -> tuple[PlanOutput, list[str]]:
    """Return validated plan + parse warnings."""
    warnings: list[str] = []
    data = extract_json_object(raw)
    if data is None:
        warnings.append("plan_parse_fallback")
        return PlanOutput(slots=list(PLAN_SLOTS)), warnings
    try:
        return PlanOutput.model_validate(data), warnings
    except ValidationError:
        warnings.append("plan_validation_fallback")
        slots = data.get("slots") if isinstance(data.get("slots"), list) else list(PLAN_SLOTS)
        return PlanOutput(slots=[s for s in slots if s in ALLOWED_SLOTS] or list(PLAN_SLOTS)), warnings


def parse_probe(raw: str) -> tuple[ProbeStep | None, list[str]]:
    warnings: list[str] = []
    data = extract_json_object(raw)
    if data is None:
        warnings.append("probe_parse_failed")
        return None, warnings
    try:
        return ProbeStep.model_validate(data), warnings
    except ValidationError as exc:
        warnings.append(f"probe_invalid:{exc.error_count()}")
        return None, warnings


def parse_synthesis(raw: str) -> tuple[SynthesisOutput, list[str]]:
    warnings: list[str] = []
    data = extract_json_object(raw)
    if data is None:
        warnings.append("synthesis_parse_fallback")
        prose = (raw or "").strip() or "Unable to parse model output."
        return SynthesisOutput(answer=prose, citations=[], gaps=["unstructured_model_output"]), warnings
    try:
        return SynthesisOutput.model_validate(data), warnings
    except ValidationError:
        warnings.append("synthesis_validation_fallback")
        answer = str(data.get("answer") or raw or "").strip() or "Empty model answer."
        cites_raw = data.get("citations") if isinstance(data.get("citations"), list) else []
        citations: list[CitationClaim] = []
        for c in cites_raw:
            if not isinstance(c, dict):
                continue
            rid = str(c.get("id") or "").strip()
            if not rid:
                continue
            citations.append(
                CitationClaim(
                    source=str(c.get("source") or ""),
                    id=rid,
                    note=str(c.get("note") or ""),
                )
            )
        gaps = [str(g) for g in (data.get("gaps") or []) if g]
        gaps.append("synthesis_schema_relaxed")
        return SynthesisOutput(answer=answer, citations=citations, gaps=gaps), warnings


def validate_answer_safety(answer: str) -> list[str]:
    """Soft output flags — never invent 'blocked'; append as gaps."""
    flags: list[str] = []
    low = (answer or "").lower()
    if re.search(r"(?i)\b(api[_-]?key|sk-[a-z0-9]{10,}|gsk_[a-z0-9]{10,})\b", answer or ""):
        flags.append("output_looks_like_secret")
    if "system prompt" in low and ("here is" in low or "is:" in low):
        flags.append("output_claims_system_prompt")
    return flags
