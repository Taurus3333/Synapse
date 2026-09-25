"""Golden evaluation — deterministic pack/citation checks, no LLM judge."""

from synapse.eval.cases import GoldenCase, atlas_q2_case, default_suite, load_suite
from synapse.eval.runner import run_case_multihop, run_suite, score_answer_fixture
from synapse.eval.score import CaseScore, SuiteReport, score_answer, score_multihop

__all__ = [
    "CaseScore",
    "GoldenCase",
    "SuiteReport",
    "atlas_q2_case",
    "default_suite",
    "load_suite",
    "run_case_multihop",
    "run_suite",
    "score_answer",
    "score_answer_fixture",
    "score_multihop",
]
