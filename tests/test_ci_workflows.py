"""Chunk 20 — CI/CD workflow layout."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_ci_workflow_has_eval_redteam_infra_gates() -> None:
    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    for needle in (
        "synapse-eval",
        "synapse-redteam",
        "pip-audit",
        "terraform validate",
        "docker build",
        "ci-ok",
        "SYNAPSE_JWT_SECRET",
        "-m e2e",
    ):
        assert needle in text, needle


def test_deploy_workflow_is_manual_only() -> None:
    text = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch" in text
    on_block = text.split("on:", 1)[1].split("jobs:", 1)[0]
    assert "workflow_dispatch" in on_block
    assert "push:" not in on_block
    assert "terraform plan" in text
    assert "push_ecr" in text
