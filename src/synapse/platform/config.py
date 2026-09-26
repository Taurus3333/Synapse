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

    # One API process. Inflight asks share the DB pool; pool must cover them plus health.
    ask_max_inflight: int = 4
    ask_max_inflight_per_tenant: int = 2
    ask_deadline_s: float = 120.0
    chat_timeout_s: float = 25.0
    embed_timeout_s: float = 20.0
    db_pool_size: int = 5
    db_max_overflow: int = 3
    db_pool_timeout_s: float = 10.0
    llm_circuit_failures: int = 5
    llm_circuit_reset_s: float = 30.0

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
        if self.ask_max_inflight < 1:
            raise ValueError("ask_max_inflight must be >= 1")
        if not 1 <= self.ask_max_inflight_per_tenant <= self.ask_max_inflight:
            raise ValueError("ask_max_inflight_per_tenant must be between 1 and ask_max_inflight")
        if self.ask_deadline_s <= self.chat_timeout_s:
            raise ValueError("ask_deadline_s must be greater than chat_timeout_s")
        if self.chat_timeout_s <= 0 or self.embed_timeout_s <= 0:
            raise ValueError("chat_timeout_s and embed_timeout_s must be > 0")
        if self.db_pool_size < 1 or self.db_max_overflow < 0 or self.db_pool_timeout_s <= 0:
            raise ValueError("database pool settings must be positive")
        # Each in-flight ask can hold one connection; health/auth needs one more.
        if self.db_pool_size + self.db_max_overflow < self.ask_max_inflight + 1:
            raise ValueError(
                "db pool (size + overflow) must be at least ask_max_inflight + 1"
            )
        if self.llm_circuit_failures < 1 or self.llm_circuit_reset_s <= 0:
            raise ValueError("llm circuit settings must be positive")
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
