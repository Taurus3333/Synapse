"""CI workflow layout — product gates only."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_ci_workflow_has_eval_redteam_gates() -> None:
    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    for needle in (
        "synapse-eval",
        "synapse-redteam",
        "pip-audit",
        "docker build",
        "ci-ok",
        "SYNAPSE_JWT_SECRET",
        "-m e2e",
    ):
        assert needle in text, needle
    assert "terraform apply" not in text
    assert "terraform validate" in text
    assert "deploy.yml" not in text


def test_no_aws_deploy_workflow() -> None:
    """CI does not apply AWS. Terraform lives in the repo for a manual apply."""
    assert not (ROOT / ".github" / "workflows" / "deploy.yml").exists()
    terraform = ROOT / "infra" / "terraform" / "main.tf"
    assert terraform.exists()
    body = terraform.read_text(encoding="utf-8")
    assert "aws_instance" in body
    assert "desired_count" not in body
