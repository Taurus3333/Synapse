"""Connector status + live readiness probes.

Catalogs live DB SQL connectors, public externals, and optional private tokens.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from synapse.auth.deps import get_principal
from synapse.auth.principal import Principal
from synapse.tools.connectors import connector_status, probe_connectors

router = APIRouter(prefix="/v1", tags=["connectors"])


@router.get("/connectors")
async def connectors(_: Principal = Depends(get_principal)) -> dict:
    """live_db + public_external + optional_private catalog (no secrets required for public)."""
    return {"providers": connector_status()}


@router.get("/connectors/ready")
async def connectors_ready(_: Principal = Depends(get_principal)) -> dict:
    """Liveness: Postgres catalog + live HTTP probes (GitHub, HN, SO, Wikipedia)."""
    return await probe_connectors()
