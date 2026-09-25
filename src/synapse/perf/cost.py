"""Cost model — published list prices used only as *estimates* over measured tokens.

Rates are documented assumptions, not invoices. Update when providers change.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PriceCard:
    """USD per 1M tokens (chat) or per 1M tokens (embeddings)."""

    name: str
    input_per_mtok: float
    output_per_mtok: float = 0.0
    notes: str = ""


# Approximate public list prices (portfolio estimate — not a billing feed).
GROQ_GPT_OSS_20B = PriceCard(
    name="groq/openai/gpt-oss-20b",
    input_per_mtok=0.10,
    output_per_mtok=0.50,
    notes="Groq list-class estimate; verify console for current tier.",
)
OPENAI_EMBED_SMALL = PriceCard(
    name="openai/text-embedding-3-small",
    input_per_mtok=0.02,
    output_per_mtok=0.0,
    notes="OpenAI list price for text-embedding-3-small.",
)


def estimate_chat_usd(*, prompt_tokens: int, completion_tokens: int, card: PriceCard) -> float:
    return (prompt_tokens / 1_000_000.0) * card.input_per_mtok + (
        completion_tokens / 1_000_000.0
    ) * card.output_per_mtok


def estimate_embed_usd(*, tokens: int, card: PriceCard = OPENAI_EMBED_SMALL) -> float:
    return (tokens / 1_000_000.0) * card.input_per_mtok


def estimate_run_usd(usage: dict[str, Any]) -> dict[str, float]:
    """Estimate USD from a usage dict produced by the agent / perf harness."""
    chat_in = int(usage.get("prompt_tokens") or 0)
    chat_out = int(usage.get("completion_tokens") or 0)
    embed_tok = int(usage.get("embed_tokens") or 0)
    chat = estimate_chat_usd(
        prompt_tokens=chat_in, completion_tokens=chat_out, card=GROQ_GPT_OSS_20B
    )
    embed = estimate_embed_usd(tokens=embed_tok)
    return {
        "chat_usd": round(chat, 6),
        "embed_usd": round(embed, 6),
        "total_usd": round(chat + embed, 6),
    }


def pricing_notes() -> dict[str, Any]:
    return {
        "chat": {
            "model": GROQ_GPT_OSS_20B.name,
            "input_usd_per_mtok": GROQ_GPT_OSS_20B.input_per_mtok,
            "output_usd_per_mtok": GROQ_GPT_OSS_20B.output_per_mtok,
            "notes": GROQ_GPT_OSS_20B.notes,
        },
        "embeddings": {
            "model": OPENAI_EMBED_SMALL.name,
            "input_usd_per_mtok": OPENAI_EMBED_SMALL.input_per_mtok,
            "notes": OPENAI_EMBED_SMALL.notes,
        },
        "disclaimer": "Estimates from list prices × measured tokens — not provider invoices.",
    }
