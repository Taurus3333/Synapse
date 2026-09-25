"""LangGraph agent graph structure tests (no LLM calls)."""

from __future__ import annotations

from synapse.agent.graph import build_agent_graph, route_after_probe


def test_graph_compiles_with_expected_nodes() -> None:
    graph = build_agent_graph()
    # Compiled graph exposes nodes via get_graph
    names = set(graph.get_graph().nodes)
    for required in {
        "plan",
        "gather",
        "follow",
        "probe",
        "finish",
        "evidence",
        "synthesise",
    }:
        assert required in names


def test_probe_routing_respects_budget_and_checklist() -> None:
    assert (
        route_after_probe(
            {"checklist": {"a": "filled"}, "probe_count": 1, "max_probe_steps": 6}
        )
        == "finish"
    )
    assert (
        route_after_probe(
            {"checklist": {"a": "empty"}, "probe_count": 6, "max_probe_steps": 6}
        )
        == "finish"
    )
    assert (
        route_after_probe(
            {"checklist": {"a": "empty"}, "probe_count": 2, "max_probe_steps": 6}
        )
        == "probe"
    )
