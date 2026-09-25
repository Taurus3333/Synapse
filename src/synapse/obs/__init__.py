"""Observability — correlation IDs, structured spans, in-process metrics.

LangSmith is intentionally not wired: STM checkpoints + tool trails + these spans
already give per-run audit without a third-party tracing SaaS.
"""

from synapse.obs.metrics import MetricsRegistry, get_metrics, reset_metrics
from synapse.obs.tracing import timed_span

__all__ = [
    "MetricsRegistry",
    "get_metrics",
    "reset_metrics",
    "timed_span",
]
