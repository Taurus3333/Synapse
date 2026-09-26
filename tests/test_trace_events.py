"""Public trace events stay free of model text."""

from synapse.agent.trace import public_events


def test_plan_event_is_slot_names_only() -> None:
    events = public_events(
        "plan",
        {"plan": ["live_status", "risks"], "raw": "model scratch that must not leak"},
    )
    assert events == [
        {
            "step": "plan",
            "label": "Plan created",
            "detail": {"slots": ["live_status", "risks"]},
        }
    ]
    assert "scratch" not in str(events)


def test_synthesis_event_counts_citations_and_hides_the_answer() -> None:
    events = public_events(
        "synthesise",
        {
            "answer": "private draft the UI must not stream as a thought",
            "citations": [{"id": "rsk_nw_00003"}],
            "rejected_citations": [],
            "gaps": ["tavily_unavailable"],
        },
    )
    assert events[0]["detail"]["citation_count"] == 1
    assert events[0]["detail"]["gap_count"] == 1
    blob = str(events)
    assert "private draft" not in blob


def test_unknown_node_is_dropped() -> None:
    assert public_events("write", {"answer": "nope"}) == []
