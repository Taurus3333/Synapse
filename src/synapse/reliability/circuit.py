"""Circuit breaker for a shared dependency that can amplify failure.

Used for chat completions. A dead provider must not be retried on every ask.
A failed Hacker News, Stack Overflow, or Tavily call stays a gap.
Those tools are outside this circuit.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Literal


class CircuitOpen(RuntimeError):
    """The dependency is failing fast. Callers must not invent a substitute answer."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"circuit_open:{name}")


class CircuitBreaker:
    """Closed → open after N consecutive failures → one half-open probe after reset_s."""

    def __init__(
        self,
        name: str,
        *,
        failure_threshold: int = 5,
        reset_s: float = 30.0,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        if reset_s <= 0:
            raise ValueError("reset_s must be > 0")
        self.name = name
        self.failure_threshold = failure_threshold
        self.reset_s = reset_s
        self._clock = clock or time.monotonic
        self._failures = 0
        self._opened_at: float | None = None
        self._half_open = False

    @property
    def state(self) -> Literal["closed", "open", "half_open"]:
        if self._opened_at is None:
            return "closed"
        if self._half_open:
            return "half_open"
        if (self._clock() - self._opened_at) >= self.reset_s:
            return "half_open"
        return "open"

    def before_call(self) -> None:
        """Admit one call, or raise CircuitOpen. Not safe to overlap; the event loop is the lock.

        Chat calls are awaited, so a second ask can interleave only at await points.
        The half-open flag is set before the await in `_chat`, which is the critical section.
        """
        if self._opened_at is None:
            return
        elapsed = self._clock() - self._opened_at
        if elapsed < self.reset_s or self._half_open:
            raise CircuitOpen(self.name)
        # Cooldown elapsed: exactly one probe. Further callers stay rejected until it settles.
        self._half_open = True

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None
        self._half_open = False

    def record_failure(self) -> None:
        self._half_open = False
        self._failures += 1
        if self._opened_at is not None or self._failures >= self.failure_threshold:
            self._opened_at = self._clock()
            self._failures = self.failure_threshold

    def abandon(self) -> None:
        """Cancelled caller: do not count a provider failure; release a half-open probe."""
        self._half_open = False


_chat_circuit: CircuitBreaker | None = None


def get_chat_circuit() -> CircuitBreaker:
    """Process-wide breaker for chat. Reset in tests via `reset_chat_circuit`."""
    global _chat_circuit
    if _chat_circuit is None:
        from synapse.platform.config import get_settings

        settings = get_settings()
        _chat_circuit = CircuitBreaker(
            "chat",
            failure_threshold=settings.llm_circuit_failures,
            reset_s=settings.llm_circuit_reset_s,
        )
    return _chat_circuit


def reset_chat_circuit() -> None:
    global _chat_circuit
    _chat_circuit = None
