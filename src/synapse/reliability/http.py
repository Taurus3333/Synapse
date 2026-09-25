"""Shared HTTP error type for resilient connector reads."""

from __future__ import annotations


class HttpStatusError(RuntimeError):
    def __init__(self, status_code: int, detail: str = "") -> None:
        self.status_code = status_code
        super().__init__(f"http_{status_code}:{detail[:200]}")
