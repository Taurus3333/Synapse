"""Chunk 18 — infra layout sanity (no AWS credentials required)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_dockerfile_exists_and_runs_synapse_api() -> None:
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "FROM python:3.12" in text
    assert "synapse-api" in text
    assert "EXPOSE 8000" in text


def test_terraform_stack_has_core_files() -> None:
    tf = ROOT / "infra" / "terraform"
    for name in (
        "versions.tf",
        "variables.tf",
        "vpc.tf",
        "security_groups.tf",
        "rds.tf",
        "redis.tf",
        "ecr.tf",
        "alb.tf",
        "ecs.tf",
        "iam.tf",
        "secrets.tf",
        "outputs.tf",
        "terraform.tfvars.example",
    ):
        assert (tf / name).is_file(), name


def test_deploy_scripts_exist() -> None:
    deploy = ROOT / "scripts" / "deploy"
    for name in (
        "apply.ps1",
        "ecr_push.ps1",
        "rollout.ps1",
        "bootstrap.ps1",
        "ecr_push.sh",
        "rollout.sh",
    ):
        assert (deploy / name).is_file(), name
    assert (ROOT / "deploy" / "prod.env.example").is_file()


def test_alb_https_is_optional_via_acm_var() -> None:
    alb = (ROOT / "infra" / "terraform" / "alb.tf").read_text(encoding="utf-8")
    assert "acm_certificate_arn" in alb
    assert "aws_lb_listener" in alb
    assert "http_redirect" in alb
    assert "https" in alb


def test_terraform_omits_sqs_eks_on_purpose() -> None:
    """Guard against accidental ceremony services in the stack."""
    blob = ""
    for path in (ROOT / "infra" / "terraform").glob("*.tf"):
        blob += path.read_text(encoding="utf-8")
    assert "aws_sqs_queue" not in blob
    assert "aws_eks_cluster" not in blob
    assert "aws_ecs_service" in blob
    assert "aws_db_instance" in blob
