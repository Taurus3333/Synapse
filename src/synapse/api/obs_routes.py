"""Observability HTTP surface — metrics snapshot."""

from __future__ import annotations

from fastapi import APIRouter

from synapse import __version__
from synapse.obs.metrics import get_metrics
from synapse.platform.logging import get_correlation_id

router = APIRouter(prefix="/v1", tags=["observability"])


@router.get("/metrics")
async def metrics_snapshot() -> dict:
    """Process-local counters and timings (no tenant secrets)."""
    snap = get_metrics().snapshot()
    return {
        "version": __version__,
        "correlation_id": get_correlation_id(),
        "counters": snap["counters"],
        "timings": snap["timings"],
        "note": (
            "In-process aggregates since process start. "
            "Per-run audit lives in STM checkpoints + tool_trail — not LangSmith."
        ),
    }
