"""Chunk 16 — observability: metrics registry, spans, HTTP headers."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from synapse.obs.metrics import get_metrics, reset_metrics
from synapse.obs.tracing import timed_span
from synapse.platform.logging import CORRELATION_HEADER, bind_correlation_id, configure_logging


@pytest.fixture(autouse=True)
def _reset_metrics() -> None:
    reset_metrics()
    yield
    reset_metrics()


def test_metrics_incr_and_observe() -> None:
    m = get_metrics()
    m.incr("asks_total", outcome="ok")
    m.incr("asks_total", outcome="ok")
    m.incr("asks_total", outcome="error")
    m.observe("ask_duration_ms", 12.5, outcome="ok")
    m.observe("ask_duration_ms", 7.5, outcome="ok")
    snap = m.snapshot()
    assert snap["counters"]["asks_total|outcome=ok"] == 2
    assert snap["counters"]["asks_total|outcome=error"] == 1
    timing = snap["timings"]["ask_duration_ms|outcome=ok"]
    assert timing["count"] == 2
    assert timing["avg_ms"] == 10.0
    assert timing["max_ms"] == 12.5


def test_timed_span_records_metric() -> None:
    configure_logging(json_logs=True, level="INFO")
    bind_correlation_id("corr-test-span-001")
    with timed_span("unit", metric="unit_span_ms", outcome="ok"):
        pass
    snap = get_metrics().snapshot()
    assert "unit_span_ms|outcome=ok" in snap["timings"]
    assert snap["timings"]["unit_span_ms|outcome=ok"]["count"] == 1


def test_metrics_endpoint_and_correlation_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYNAPSE_JWT_SECRET", "integration-test-synapse-jwt-secret!!")
    monkeypatch.setenv("SYNAPSE_ENV", "test")
    monkeypatch.setenv(
        "SYNAPSE_DATABASE_URL",
        "postgresql+asyncpg://synapse:synapse@127.0.0.1:5432/synapse",
    )
    monkeypatch.setenv("SYNAPSE_REDIS_URL", "redis://127.0.0.1:6379/0")
    from synapse.platform.config import clear_settings_cache

    clear_settings_cache()

    from synapse.api.app import create_app

    # Avoid requiring live stores for this route smoke — health needs them;
    # metrics does not, but lifespan still connects if connect_stores=True.
    # Use connect_stores=False and only hit /v1/metrics.
    app = create_app(connect_stores=False)
    with TestClient(app) as client:
        r = client.get("/v1/metrics", headers={CORRELATION_HEADER: "corr-metrics-abc12"})
        assert r.status_code == 200, r.text
        assert r.headers.get(CORRELATION_HEADER)
        assert "X-Request-Duration-Ms" in r.headers
        body = r.json()
        assert "counters" in body
        assert "timings" in body
        assert "LangSmith" in body["note"]
        # Middleware records after the handler returns — second call sees the first.
        r2 = client.get("/v1/metrics")
        assert r2.status_code == 200
        counters = r2.json()["counters"]
        assert any(k.startswith("http_requests_total|") for k in counters)
        assert any(k.startswith("http_request_duration_ms|") for k in r2.json()["timings"])
    clear_settings_cache()
