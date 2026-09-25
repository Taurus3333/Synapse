from __future__ import annotations

from time import perf_counter

from redis.asyncio import Redis

from synapse.platform.logging import get_logger

logger = get_logger(__name__)


class RedisClient:
    """Redis client for cache, counters, and locks. Never the source of truth."""

    def __init__(self, url: str) -> None:
        self._url = url
        self._client: Redis | None = None

    @property
    def client(self) -> Redis:
        if self._client is None:
            raise RuntimeError("redis client is not connected")
        return self._client

    async def connect(self) -> None:
        if self._client is not None:
            return
        self._client = Redis.from_url(self._url, decode_responses=True)
        logger.info("redis_client_created")

    async def close(self) -> None:
        if self._client is None:
            return
        await self._client.aclose()
        self._client = None
        logger.info("redis_client_closed")

    async def ping(self) -> float:
        started = perf_counter()
        ok = await self.client.ping()
        if not ok:
            raise RuntimeError("redis ping returned false")
        return (perf_counter() - started) * 1000
