"""OpenAI embeddings with content-hash cache. Requires SYNAPSE_OPENAI_API_KEY."""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from openai import AsyncOpenAI

from synapse.reliability.retry import RetryPolicy, with_retry

if TYPE_CHECKING:
    from synapse.perf.usage import UsageAccumulator

EMBED_MODEL = "text-embedding-3-small"
EMBED_DIM = 1536


class Embedder:
    def __init__(
        self,
        api_key: str,
        *,
        usage: UsageAccumulator | None = None,
        timeout_s: float = 20.0,
    ) -> None:
        if not api_key:
            raise RuntimeError("SYNAPSE_OPENAI_API_KEY is required for embeddings")
        self._client = AsyncOpenAI(api_key=api_key, timeout=timeout_s)
        self._cache: dict[str, list[float]] = {}
        self.usage = usage

    @staticmethod
    def content_hash(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    async def embed_one(self, text: str) -> list[float]:
        vectors = await self.embed_many([text])
        return vectors[0]

    async def embed_many(self, texts: Sequence[str], *, batch_size: int = 64) -> list[list[float]]:
        out: list[list[float] | None] = [None] * len(texts)
        pending_idx: list[int] = []
        pending_text: list[str] = []
        for i, text in enumerate(texts):
            key = self.content_hash(text)
            cached = self._cache.get(key)
            if cached is not None:
                out[i] = cached
                if self.usage is not None:
                    self.usage.add_embed(tokens=0, cached=True)
            else:
                pending_idx.append(i)
                pending_text.append(text)
        for start in range(0, len(pending_text), batch_size):
            batch = pending_text[start : start + batch_size]
            if not batch:
                continue

            captured = list(batch)

            async def _once(b: list[str] = captured) -> Any:
                return await self._client.embeddings.create(model=EMBED_MODEL, input=b)

            resp = await with_retry(_once, policy=RetryPolicy(attempts=3, base_delay_s=0.3))
            batch_tokens = 0
            if getattr(resp, "usage", None) is not None:
                batch_tokens = int(getattr(resp.usage, "total_tokens", 0) or 0)
            per = batch_tokens // max(len(batch), 1) if batch_tokens else 0
            for offset, item in enumerate(resp.data):
                idx = pending_idx[start + offset]
                vec = list(item.embedding)
                self._cache[self.content_hash(batch[offset])] = vec
                out[idx] = vec
                if self.usage is not None:
                    # Last item absorbs remainder so sum matches API total.
                    tok = per
                    if offset == len(resp.data) - 1 and batch_tokens:
                        tok = batch_tokens - per * (len(batch) - 1)
                    self.usage.add_embed(tokens=tok, cached=False)
        return [v if v is not None else [] for v in out]
