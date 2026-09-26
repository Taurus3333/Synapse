"""Usage accounting shared by agent chat + embeddings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class UsageAccumulator:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    chat_calls: int = 0
    embed_tokens: int = 0
    embed_calls: int = 0
    embed_cache_hits: int = 0

    def add_chat(self, *, prompt: int = 0, completion: int = 0) -> None:
        self.prompt_tokens += max(0, prompt)
        self.completion_tokens += max(0, completion)
        self.chat_calls += 1

    def add_embed(self, *, tokens: int = 0, cached: bool = False) -> None:
        if cached:
            self.embed_cache_hits += 1
            return
        self.embed_tokens += max(0, tokens)
        self.embed_calls += 1

    def merge(self, other: UsageAccumulator) -> None:
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.chat_calls += other.chat_calls
        self.embed_tokens += other.embed_tokens
        self.embed_calls += other.embed_calls
        self.embed_cache_hits += other.embed_cache_hits

    def as_dict(self) -> dict[str, int]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "chat_calls": self.chat_calls,
            "embed_tokens": self.embed_tokens,
            "embed_calls": self.embed_calls,
            "embed_cache_hits": self.embed_cache_hits,
            "total_tokens": self.prompt_tokens + self.completion_tokens + self.embed_tokens,
        }


def percentile(sorted_vals: list[float], p: float) -> float:
    """Nearest-rank percentile on a pre-sorted list."""
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return sorted_vals[f]
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def summarize_latencies(samples_ms: list[float]) -> dict[str, Any]:
    vals = sorted(float(x) for x in samples_ms)
    return {
        "n": len(vals),
        "min_ms": round(vals[0], 2) if vals else 0.0,
        "p50_ms": round(percentile(vals, 50), 2),
        "p95_ms": round(percentile(vals, 95), 2),
        "max_ms": round(vals[-1], 2) if vals else 0.0,
        "mean_ms": round(sum(vals) / len(vals), 2) if vals else 0.0,
    }
