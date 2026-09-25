"""Chunk 22 — docs cite measured artifacts that exist."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"


def test_measured_artifacts_exist_and_pass() -> None:
    demo = json.loads((DOCS / "demo_results.json").read_text(encoding="utf-8"))
    assert demo.get("passed") is True
    assert int(demo.get("n_passed") or 0) >= 13

    perf = json.loads((DOCS / "perf_results.json").read_text(encoding="utf-8"))
    ask = next(s for s in perf["scenarios"] if s["id"] == "ask_atlas_q2")
    assert ask["latency"]["p50_ms"] > 0
    assert ask["usage_totals"]["n_ok"] >= 1

    e2e = json.loads((DOCS / "e2e_results.json").read_text(encoding="utf-8"))
    assert e2e.get("passed") is True
    assert int(e2e.get("n_passed") or 0) == int(e2e.get("n_cases") or 0)


def test_three_docs_exist_and_agree_on_roles() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    arch = (ROOT / "ARCHITECTURE.md").read_text(encoding="utf-8")
    project = (ROOT / "PROJECT.md").read_text(encoding="utf-8")
    assert "docs/e2e_results.json" in readme
    assert "docs/perf_results.json" in readme
    assert "Package map" in arch or "Package map" in arch.replace("**", "")
    assert "Chunk 22" in project
    assert "no invented metrics" in project.lower() or "No invented metrics" in project
    assert "Deliberate non-goals" in readme or "non-goals" in readme.lower()
