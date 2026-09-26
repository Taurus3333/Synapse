"""Local runtime packaging sanity (Dockerfile + Compose)."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_dockerfile_exists_and_runs_synapse_api() -> None:
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "FROM python:3.12" in text
    assert "synapse-api" in text
    assert "EXPOSE 8000" in text


def test_compose_defines_postgres_redis() -> None:
    text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "postgres" in text.lower() or "pgvector" in text.lower()
    assert "redis" in text.lower()


def test_aws_compose_runs_one_api_and_the_ui() -> None:
    text = (ROOT / "docker-compose.aws.yml").read_text(encoding="utf-8")
    assert "synapse-ui" in text
    assert "8000:8000" in text
    assert "8501:8501" in text
