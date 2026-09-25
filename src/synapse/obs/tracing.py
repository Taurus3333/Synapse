"""Structured spans — duration events bound to correlation_id (and optional run_id).

STM checkpoints remain the durable audit trail. Spans are the live log view of
the same phases so operators can grep one correlation_id end-to-end.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from synapse.obs.metrics import get_metrics
from synapse.platform.logging import get_correlation_id, get_logger

logger = get_logger(__name__)


@contextmanager
def timed_span(
    name: str,
    *,
    metric: str | None = None,
    **fields: Any,
) -> Iterator[dict[str, Any]]:
    """Log span start/end and optionally observe a timing metric."""
    started = time.perf_counter()
    payload: dict[str, Any] = {
        "span": name,
        "correlation_id": get_correlation_id(),
        **{k: v for k, v in fields.items() if v is not None},
    }
    logger.info("span_start", **payload)
    exc_name: str | None = None
    try:
        yield payload
    except Exception as exc:
        exc_name = type(exc).__name__
        raise
    finally:
        duration_ms = (time.perf_counter() - started) * 1000.0
        payload["duration_ms"] = round(duration_ms, 3)
        if exc_name:
            payload["error"] = exc_name
            logger.warning("span_error", **payload)
        else:
            logger.info("span_end", **payload)
        if metric:
            labels = {
                k: str(v)
                for k, v in fields.items()
                if k in {"outcome", "tool", "route", "method", "status_class"} and v is not None
            }
            get_metrics().observe(metric, duration_ms, **labels)
