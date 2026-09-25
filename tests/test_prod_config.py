"""Chunk 19 — production settings hardening."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from synapse.platform.config import Settings, clear_settings_cache


def _base(**overrides: object) -> Settings:
    clear_settings_cache()
    data = {
        "env": "local",
        "database_url": "postgresql+asyncpg://u:p@localhost:5432/synapse",
        "redis_url": "redis://localhost:6379/0",
        "jwt_secret": "dev-only-change-me-synapse-jwt-32chars!!",
        "api_host": "127.0.0.1",
        "groq_api_key": None,
        "openai_api_key": None,
    }
    data.update(overrides)
    # Ignore process .env so local keys don't mask prod hardening checks.
    return Settings(_env_file=None, **data)  # type: ignore[arg-type]


def test_local_allows_dev_jwt() -> None:
    s = _base()
    assert s.env == "local"


def test_prod_rejects_dev_jwt() -> None:
    with pytest.raises(ValidationError, match="jwt_secret"):
        _base(
            env="prod",
            api_host="0.0.0.0",
            groq_api_key="gsk_test",
            jwt_secret="dev-only-change-me-synapse-jwt-32chars!!",
        )


def test_prod_rejects_localhost_bind() -> None:
    with pytest.raises(ValidationError, match="api_host"):
        _base(
            env="prod",
            api_host="127.0.0.1",
            groq_api_key="gsk_test",
            jwt_secret="prod-grade-secret-key-32chars-min!!",
        )


def test_prod_requires_chat_key() -> None:
    with pytest.raises(ValidationError, match="GROQ|OPENAI|chat"):
        _base(
            env="prod",
            api_host="0.0.0.0",
            jwt_secret="prod-grade-secret-key-32chars-min!!",
        )


def test_prod_accepts_hardened_config() -> None:
    s = _base(
        env="prod",
        api_host="0.0.0.0",
        jwt_secret="prod-grade-secret-key-32chars-min!!",
        groq_api_key="gsk_test",
        database_url="postgresql+asyncpg://u:p@db:5432/synapse?ssl=require",
    )
    assert s.env == "prod"
    assert "ssl=require" in s.database_dsn()
