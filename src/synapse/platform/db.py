from __future__ import annotations

from collections.abc import Mapping
from time import perf_counter

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from synapse.platform.logging import get_logger

logger = get_logger(__name__)


class Database:
    """Async SQLAlchemy engine. No models live here — that is Chunk 3."""

    def __init__(self, url: str) -> None:
        self._url = url
        self._engine: AsyncEngine | None = None

    @property
    def engine(self) -> AsyncEngine:
        if self._engine is None:
            raise RuntimeError("database engine is not connected")
        return self._engine

    async def connect(self) -> None:
        if self._engine is not None:
            return
        self._engine = create_async_engine(self._url, pool_pre_ping=True)
        logger.info("database_engine_created")

    async def close(self) -> None:
        if self._engine is None:
            return
        await self._engine.dispose()
        self._engine = None
        logger.info("database_engine_closed")

    async def ping(self) -> float:
        started = perf_counter()
        async with self.engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        return (perf_counter() - started) * 1000


def health_error_name(exc: BaseException) -> str:
    """Return a leak-safe error label. Never include the exception message."""
    return type(exc).__name__


def redact_secrets(values: Mapping[str, object], secret_keys: frozenset[str]) -> dict[str, object]:
    redacted: dict[str, object] = {}
    for key, value in values.items():
        redacted[key] = "***" if key in secret_keys else value
    return redacted
