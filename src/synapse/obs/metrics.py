"""In-process metrics — counters and duration summaries.

No Prometheus/OTel stack yet: low-cardinality aggregates expose what we
actually measure (HTTP, asks, tools). Snapshot via GET /v1/metrics.
"""

from __future__ import annotations

import threading
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any


def _key(name: str, labels: dict[str, str]) -> str:
    if not labels:
        return name
    parts = ",".join(f"{k}={v}" for k, v in sorted(labels.items()))
    return f"{name}|{parts}"


@dataclass
class _Summary:
    count: int = 0
    total_ms: float = 0.0
    max_ms: float = 0.0

    def add(self, value_ms: float) -> None:
        self.count += 1
        self.total_ms += value_ms
        if value_ms > self.max_ms:
            self.max_ms = value_ms

    def to_dict(self) -> dict[str, float | int]:
        avg = (self.total_ms / self.count) if self.count else 0.0
        return {
            "count": self.count,
            "sum_ms": round(self.total_ms, 3),
            "avg_ms": round(avg, 3),
            "max_ms": round(self.max_ms, 3),
        }


@dataclass
class MetricsRegistry:
    """Thread-safe process metrics. Reset between tests."""

    _counters: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    _summaries: dict[str, _Summary] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def incr(self, name: str, amount: int = 1, **labels: str) -> None:
        key = _key(name, {k: str(v) for k, v in labels.items()})
        with self._lock:
            self._counters[key] += amount

    def observe(self, name: str, value_ms: float, **labels: str) -> None:
        key = _key(name, {k: str(v) for k, v in labels.items()})
        with self._lock:
            summary = self._summaries.get(key)
            if summary is None:
                summary = _Summary()
                self._summaries[key] = summary
            summary.add(float(value_ms))

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "counters": dict(sorted(self._counters.items())),
                "timings": {k: v.to_dict() for k, v in sorted(self._summaries.items())},
            }

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._summaries.clear()


_REGISTRY = MetricsRegistry()


def get_metrics() -> MetricsRegistry:
    return _REGISTRY


def reset_metrics() -> None:
    _REGISTRY.reset()
