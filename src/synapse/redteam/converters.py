"""PyRIT converters (preferred) + local fallbacks for obfuscation mutations."""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Mutation:
    name: str
    text: str
    via_pyrit: bool


def _local_mutations(prompt: str) -> list[Mutation]:
    spaced = " ".join(prompt)
    leet = (
        prompt.replace("a", "4")
        .replace("e", "3")
        .replace("i", "1")
        .replace("o", "0")
        .replace("s", "5")
        .replace("t", "7")
    )
    flipped = prompt[::-1]
    b64 = base64.b64encode(prompt.encode("utf-8")).decode("ascii")
    return [
        Mutation("identity", prompt, False),
        Mutation("local_base64", f"Please decode and follow: {b64}", False),
        Mutation("local_leet", leet, False),
        Mutation("local_charspace", spaced, False),
        Mutation("local_flip", flipped, False),
    ]


async def mutate_prompt(prompt: str) -> list[Mutation]:
    """Expand one seed into converter mutations. Uses PyRIT when importable."""
    mutations = _local_mutations(prompt)
    try:
        from pyrit.prompt_converter import (  # type: ignore[import-untyped]
            Base64Converter,
            CharacterSpaceConverter,
            FlipConverter,
            LeetspeakConverter,
        )
    except Exception as exc:  # pragma: no cover - optional dep path
        logger.info("pyrit converters unavailable: %s", exc)
        return mutations

    specs: list[tuple[str, object]] = [
        ("pyrit_base64", Base64Converter()),
        ("pyrit_leet", LeetspeakConverter(deterministic=True)),
        ("pyrit_charspace", CharacterSpaceConverter()),
        ("pyrit_flip", FlipConverter()),
    ]
    for name, converter in specs:
        try:
            result = await converter.convert_async(prompt=prompt)  # type: ignore[attr-defined]
            text = getattr(result, "output_text", None) or str(result)
            if name == "pyrit_base64":
                text = f"Please decode and follow: {text}"
            mutations.append(Mutation(name, text, True))
        except Exception as exc:  # pragma: no cover
            logger.warning("converter %s failed: %s", name, exc)
    return mutations


def pyrit_available() -> bool:
    try:
        import pyrit  # noqa: F401

        return True
    except Exception:
        return False
