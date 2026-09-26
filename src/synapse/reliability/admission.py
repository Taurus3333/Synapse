"""In-process admission for expensive synchronous asks.

One API process. A semaphore matches that topology. Redis is the wrong store
until more than one process accepts asks — a volatile counter must not become
the spend gate, and Redis here is a health signal only.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal


class AdmissionFull(Exception):
    def __init__(self, scope: Literal["process", "tenant"]) -> None:
        self.scope = scope
        super().__init__(f"ask_capacity:{scope}")


class AskAdmission:
    """Global inflight cap plus a per-tenant cap so one company cannot take every slot."""

    def __init__(self, *, max_inflight: int, max_per_tenant: int) -> None:
        if max_inflight < 1:
            raise ValueError("max_inflight must be >= 1")
        if max_per_tenant < 1 or max_per_tenant > max_inflight:
            raise ValueError("max_per_tenant must be between 1 and max_inflight")
        self.max_inflight = max_inflight
        self.max_per_tenant = max_per_tenant
        self._sem = asyncio.Semaphore(max_inflight)
        self._by_tenant: dict[str, int] = {}
        self._guard = asyncio.Lock()

    @asynccontextmanager
    async def acquire(self, tenant_id: str) -> AsyncIterator[None]:
        try:
            await asyncio.wait_for(self._sem.acquire(), timeout=0.05)
        except TimeoutError as exc:
            raise AdmissionFull("process") from exc
        held = False
        try:
            async with self._guard:
                current = self._by_tenant.get(tenant_id, 0)
                if current >= self.max_per_tenant:
                    raise AdmissionFull("tenant")
                self._by_tenant[tenant_id] = current + 1
                held = True
            yield
        finally:
            if held:
                async with self._guard:
                    left = self._by_tenant.get(tenant_id, 0) - 1
                    if left <= 0:
                        self._by_tenant.pop(tenant_id, None)
                    else:
                        self._by_tenant[tenant_id] = left
            self._sem.release()
