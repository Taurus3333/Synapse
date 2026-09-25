"""Minimal corpus checks — no suite bloat."""

from synapse.data import generate, open_blockers, project_named, slipped_tasks
from synapse.domain.dataset import assert_referential_integrity


def test_ci_reproducible_and_atlas_planted() -> None:
    a = generate(42, "ci")
    b = generate(42, "ci")
    assert a.fingerprint() == b.fingerprint()
    assert_referential_integrity(a)
    atlas = project_named(a, "northwind", "ATLAS")
    assert len(slipped_tasks(a, atlas.id)) >= 4
    assert open_blockers(a, atlas.id)
