"""Retry with exponential backoff — only for idempotent / transient failures."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class RetryPolicy:
    attempts: int = 3
    base_delay_s: float = 0.25
    max_delay_s: float = 4.0
    jitter: float = 0.2


def is_retryable_http_status(status: int) -> bool:
    return status in {408, 425, 429, 500, 502, 503, 504}


def is_retryable_exception(exc: BaseException) -> bool:
    name = type(exc).__name__
    # httpx / openai style transient failures
    if name in {
        "TimeoutException",
        "ConnectTimeout",
        "ReadTimeout",
        "WriteTimeout",
        "PoolTimeout",
        "NetworkError",
        "ConnectError",
        "RemoteProtocolError",
        "APIConnectionError",
        "RateLimitError",
        "InternalServerError",
        "APITimeoutError",
    }:
        return True
    # OpenAI SDK often wraps status
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if isinstance(status, int) and is_retryable_http_status(status):
        return True
    return False


async def with_retry(
    fn: Callable[[], Awaitable[T]],
    *,
    policy: RetryPolicy | None = None,
    retry_if: Callable[[BaseException], bool] | None = None,
) -> T:
    """Run async fn with backoff. Last exception is raised."""
    policy = policy or RetryPolicy()
    predicate = retry_if or is_retryable_exception
    last: BaseException | None = None
    for attempt in range(policy.attempts):
        try:
            return await fn()
        except BaseException as exc:  # noqa: BLE001 — classified below
            last = exc
            if attempt >= policy.attempts - 1 or not predicate(exc):
                raise
            delay = min(policy.max_delay_s, policy.base_delay_s * (2**attempt))
            delay *= 1.0 + random.uniform(-policy.jitter, policy.jitter)
            await asyncio.sleep(max(0.05, delay))
    assert last is not None
    raise last
