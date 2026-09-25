"""Guardrails — input policy + structured LLM output validation.

Chunk 12: keep the model inside a cage before tools run and after it speaks.
Prompt-injection framing (DATA wrappers) already exists in evidence; this layer
covers *user* input and *model JSON* contracts.
"""

from __future__ import annotations

from synapse.guardrails.input_policy import InputVerdict, check_user_question
from synapse.guardrails.structured import (
    PlanOutput,
    ProbeStep,
    SynthesisOutput,
    parse_plan,
    parse_probe,
    parse_synthesis,
)

__all__ = [
    "InputVerdict",
    "PlanOutput",
    "ProbeStep",
    "SynthesisOutput",
    "check_user_question",
    "parse_plan",
    "parse_probe",
    "parse_synthesis",
]
