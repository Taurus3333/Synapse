"""Run the Synapse red-team suite (PyRIT converters + deterministic scorers)."""

from __future__ import annotations

from synapse.redteam.attacks import (
    AttackCase,
    all_static_attacks,
    benign_controls,
    cross_tenant,
    grounding_attacks,
    indirect_injection,
)
from synapse.redteam.converters import mutate_prompt, pyrit_available
from synapse.redteam.scorers import (
    RedTeamReport,
    score_benign_allow,
    score_framing,
    score_grounding,
    score_policy_block,
)


async def run_policy_suite(cases: list[AttackCase] | None = None) -> RedTeamReport:
    """Score user-channel attacks (plain + converter mutations) against input policy."""
    report = RedTeamReport(pyrit_used=pyrit_available())
    seeds = cases or [c for c in all_static_attacks() if c.expect == "block"]
    for case in seeds:
        for mut in await mutate_prompt(case.prompt):
            report.scores.append(
                score_policy_block(
                    case, mut.text, mutation=mut.name, via_pyrit=mut.via_pyrit
                )
            )
    return report


async def run_obfuscation_suite() -> RedTeamReport:
    from synapse.redteam.attacks import obfuscation_seeds

    report = RedTeamReport(pyrit_used=pyrit_available())
    for case in obfuscation_seeds():
        for mut in await mutate_prompt(case.prompt):
            report.scores.append(
                score_policy_block(
                    case, mut.text, mutation=mut.name, via_pyrit=mut.via_pyrit
                )
            )
    return report


def run_framing_suite() -> RedTeamReport:
    report = RedTeamReport(pyrit_used=False)
    for case in indirect_injection():
        report.scores.append(score_framing(case))
    return report


def run_grounding_suite() -> RedTeamReport:
    report = RedTeamReport(pyrit_used=False)
    for case in grounding_attacks():
        report.scores.append(score_grounding(case))
    return report


def run_benign_suite() -> RedTeamReport:
    report = RedTeamReport(pyrit_used=False)
    for case in benign_controls():
        report.scores.append(score_benign_allow(case, case.prompt))
    return report


async def run_full_suite() -> RedTeamReport:
    """Aggregate policy (+mutations), framing, grounding, benign controls."""
    report = RedTeamReport(pyrit_used=pyrit_available())
    parts = [
        await run_policy_suite(),
        run_framing_suite(),
        run_grounding_suite(),
        run_benign_suite(),
    ]
    for part in parts:
        report.scores.extend(part.scores)
        report.pyrit_used = report.pyrit_used or part.pyrit_used
    return report


def tenant_http_case() -> AttackCase:
    return next(c for c in cross_tenant() if c.expect == "tenant_deny")
