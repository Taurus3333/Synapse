"""Shared HTTP GET with timeout + retry (idempotent reads only)."""

from __future__ import annotations

from typing import Any

import httpx

from synapse.reliability.retry import RetryPolicy, is_retryable_http_status, with_retry

DEFAULT_TIMEOUT = httpx.Timeout(20.0, connect=5.0)
USER_AGENT = "SynapsePortfolioAgent/1.0 (+https://github.com/synapse-local; demo)"


class HttpStatusError(RuntimeError):
    def __init__(self, status_code: int, detail: str = "") -> None:
        self.status_code = status_code
        super().__init__(f"http_{status_code}:{detail[:200]}")


async def get_json(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: httpx.Timeout | float | None = None,
    policy: RetryPolicy | None = None,
) -> dict[str, Any] | list[Any]:
    """GET JSON with retries on 429/5xx and network blips. Not for POST mutations."""
    to = DEFAULT_TIMEOUT if timeout is None else timeout
    hdrs = {"User-Agent": USER_AGENT, **(headers or {})}

    async def _once() -> dict[str, Any] | list[Any]:
        async with httpx.AsyncClient(timeout=to) as client:
            r = await client.get(url, params=params, headers=hdrs)
            if is_retryable_http_status(r.status_code):
                raise HttpStatusError(r.status_code, r.text)
            if r.status_code >= 400:
                # Non-retryable client errors — return shape callers already handle
                return {"_http_error": r.status_code, "_detail": r.text[:300]}
            data = r.json()
            return data

    return await with_retry(_once, policy=policy or RetryPolicy(attempts=3))


async def get_response_status(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: httpx.Timeout | float | None = None,
) -> tuple[int, Any]:
    """Single-shot GET for probes (no retry storm on health checks)."""
    to = DEFAULT_TIMEOUT if timeout is None else timeout
    hdrs = {"User-Agent": USER_AGENT, **(headers or {})}
    async with httpx.AsyncClient(timeout=to) as client:
        r = await client.get(url, params=params, headers=hdrs)
        try:
            body = r.json()
        except Exception:
            body = r.text[:300]
        return r.status_code, body
