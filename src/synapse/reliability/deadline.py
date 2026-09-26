"""Parent deadline for one synchronous ask.

The UI client waits 180s. The server deadline is shorter so a hung provider
returns a controlled failure instead of working after the client has gone.
"""

from __future__ import annotations


class AskDeadlineExceeded(TimeoutError):
    def __init__(self, deadline_s: float) -> None:
        self.deadline_s = deadline_s
        super().__init__(f"ask_deadline_exceeded:{deadline_s}")
