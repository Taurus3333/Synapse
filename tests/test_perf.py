"""Chunk 17 — usage accounting + latency helpers."""

from __future__ import annotations

from synapse.perf.cost import estimate_run_usd
from synapse.perf.usage import UsageAccumulator, percentile, summarize_latencies


def test_usage_accumulator_merges_chat_and_embed() -> None:
    a = UsageAccumulator()
    a.add_chat(prompt=100, completion=20)
    a.add_embed(tokens=8, cached=False)
    a.add_embed(tokens=0, cached=True)
    d = a.as_dict()
    assert d["prompt_tokens"] == 100
    assert d["completion_tokens"] == 20
    assert d["chat_calls"] == 1
    assert d["embed_tokens"] == 8
    assert d["embed_calls"] == 1
    assert d["embed_cache_hits"] == 1
    assert d["total_tokens"] == 128


def test_estimate_run_usd_is_positive_for_nonzero_tokens() -> None:
    cost = estimate_run_usd({"prompt_tokens": 1_000_000, "completion_tokens": 0, "embed_tokens": 0})
    assert cost["chat_usd"] > 0
    assert cost["total_usd"] == cost["chat_usd"]


def test_percentile_and_latency_summary() -> None:
    assert percentile([10.0, 20.0, 30.0], 50) == 20.0
    summary = summarize_latencies([10.0, 20.0, 30.0, 40.0])
    assert summary["n"] == 4
    assert summary["min_ms"] == 10.0
    assert summary["max_ms"] == 40.0
    assert summary["p50_ms"] == 25.0
