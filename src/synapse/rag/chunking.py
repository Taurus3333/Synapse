"""Chunk text into ~512-token windows with overlap. Token ≈ whitespace word here."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TextChunk:
    index: int
    text: str
    token_estimate: int


def chunk_text(text: str, *, size: int = 400, overlap: int = 50) -> list[TextChunk]:
    words = text.split()
    if not words:
        return []
    if len(words) <= size:
        return [TextChunk(index=0, text=" ".join(words), token_estimate=len(words))]
    chunks: list[TextChunk] = []
    start = 0
    index = 0
    step = max(size - overlap, 1)
    while start < len(words):
        end = min(start + size, len(words))
        piece = words[start:end]
        chunks.append(TextChunk(index=index, text=" ".join(piece), token_estimate=len(piece)))
        if end >= len(words):
            break
        start += step
        index += 1
    return chunks
