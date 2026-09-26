"""Connector status + live readiness probes.

Catalogs live Postgres tools plus Hacker News, Stack Overflow, and Tavily.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from synapse.auth.deps import get_principal
from synapse.auth.principal import Principal
from synapse.tools.connectors import connector_status, probe_connectors

router = APIRouter(prefix="/v1", tags=["connectors"])


@router.get("/connectors")
async def connectors(_: Principal = Depends(get_principal)) -> dict:
    """live_db + public_external catalog. HN and Stack Overflow need no key."""
    return {"providers": connector_status()}


@router.get("/connectors/ready")
async def connectors_ready(_: Principal = Depends(get_principal)) -> dict:
    """Liveness: Postgres catalog plus HN and Stack Overflow. Tavily only if a key is set."""
    return await probe_connectors()
