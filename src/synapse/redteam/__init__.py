"""Red-team / PyRIT-style injection suite for Synapse defenses."""

from synapse.redteam.converters import mutate_prompt, pyrit_available
from synapse.redteam.runner import run_full_suite, run_obfuscation_suite
from synapse.redteam.scorers import RedTeamReport

__all__ = [
    "RedTeamReport",
    "mutate_prompt",
    "pyrit_available",
    "run_full_suite",
    "run_obfuscation_suite",
]
