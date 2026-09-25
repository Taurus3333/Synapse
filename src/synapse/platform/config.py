from functools import lru_cache
from typing import Literal, Self

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed process configuration. Identity and secrets come from the environment."""

    model_config = SettingsConfigDict(
        env_prefix="SYNAPSE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: Literal["local", "test", "ci", "prod"] = "local"
    log_level: str = "INFO"
    log_json: bool = True
    api_host: str = "127.0.0.1"
    api_port: int = 8000

    database_url: SecretStr
    redis_url: SecretStr

    groq_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None

    jwt_secret: SecretStr = SecretStr("dev-only-change-me-synapse-jwt-32chars")
    jwt_ttl_minutes: int = 60
    demo_password: str = "synapse-demo"

    @field_validator("log_level")
    @classmethod
    def _normalise_log_level(cls, value: str) -> str:
        level = value.upper()
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        if level not in allowed:
            raise ValueError(f"log_level must be one of {sorted(allowed)}")
        return level

    @field_validator("groq_api_key", "openai_api_key", mode="before")
    @classmethod
    def _empty_optional_secret(cls, value: object) -> object:
        if value == "":
            return None
        return value

    @field_validator("jwt_ttl_minutes")
    @classmethod
    def _ttl_bounds(cls, value: int) -> int:
        if value < 5 or value > 24 * 60:
            raise ValueError("jwt_ttl_minutes must be between 5 and 1440")
        return value

    @model_validator(mode="after")
    def _reject_blank_required_urls(self) -> Self:
        if not self.database_url.get_secret_value().strip():
            raise ValueError("database_url must not be blank")
        if not self.redis_url.get_secret_value().strip():
            raise ValueError("redis_url must not be blank")
        if len(self.jwt_secret.get_secret_value()) < 32:
            raise ValueError("jwt_secret must be at least 32 characters")
        return self

    @model_validator(mode="after")
    def _prod_hardening(self) -> Self:
        """Refuse known-dev secrets and missing chat keys when SYNAPSE_ENV=prod."""
        if self.env != "prod":
            return self
        jwt = self.jwt_secret.get_secret_value()
        if jwt.startswith("dev-only") or jwt.startswith("REPLACE-ME"):
            raise ValueError("prod jwt_secret must not use the local/placeholder value")
        if self.groq_api_key is None and self.openai_api_key is None:
            raise ValueError("prod requires SYNAPSE_GROQ_API_KEY or SYNAPSE_OPENAI_API_KEY")
        if self.api_host in {"127.0.0.1", "localhost"}:
            raise ValueError("prod api_host must bind on 0.0.0.0 (or a routable interface)")
        return self

    def database_dsn(self) -> str:
        return self.database_url.get_secret_value()

    def redis_dsn(self) -> str:
        return self.redis_url.get_secret_value()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    # Fields without defaults come from the environment via BaseSettings.
    return Settings()  # type: ignore[call-arg]


def clear_settings_cache() -> None:
    get_settings.cache_clear()
