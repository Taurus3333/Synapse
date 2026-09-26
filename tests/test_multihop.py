"""Chunk 11 — deterministic multi-hop follow planner + ATLAS integration."""

from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio

from synapse.agent.graph import build_agent_graph
from synapse.agent.integrate import explain_atlas_hops, run_multihop_integration
from synapse.agent.multihop import plan_follow_hops
from synapse.auth.principal import Principal
from synapse.data.catalog import ATLAS_CUTOVER_ASK
from synapse.data.generate import generate
from synapse.evidence.types import SourceKind
from synapse.memory.ltm import LongTermMemory
from synapse.platform.config import clear_settings_cache, get_settings
from synapse.platform.db import Database
from synapse.platform.seed import create_schema, load_dataset, session_factory
from synapse.tools.runtime import ToolSession


def test_graph_includes_follow_between_gather_and_probe() -> None:
    names = set(build_agent_graph().get_graph().nodes)
    assert "follow" in names
    for required in {"plan", "gather", "follow", "probe", "finish", "evidence", "synthesise"}:
        assert required in names


def test_plan_follow_hops_from_live_risks() -> None:
    hops = plan_follow_hops(
        project_key="ATLAS",
        question="Summarize Q2 changes and major risks",
        tool_results=[
            {
                "tool": "project_lookup",
                "result": {"found": True, "status": "at_risk", "key": "ATLAS"},
            },
            {
                "tool": "risk_list",
                "result": {
                    "risks": [
                        {
                            "id": "rsk_sdk",
                            "title": "Vendor SDK miss threatens Atlas Q3 cutover",
                            "status": "open",
                        }
                    ]
                },
            },
            {
                "tool": "blocker_list",
                "result": {
                    "blockers": [
                        {
                            "id": "blk_sdk",
                            "title": "Vendor SDK not ready for the freeze",
                            "status": "open",
                        }
                    ]
                },
            },
        ],
    )
    tools = [h.tool for h in hops]
    assert "document_search" in tools
    assert "email_search" in tools
    assert "meeting_search" in tools
    assert "memory_search" in tools
    assert "hn_search" in tools
    assert "stackoverflow_search" in tools
    assert "tavily_search" in tools
    assert any("rsk_sdk" in h.source_ids for h in hops)
    assert any("live risks" in h.reason or "cross-source" in h.reason for h in hops)
    # Query must carry signal tokens from titles (not the raw question alone)
    doc = next(h for h in hops if h.tool == "document_search")
    assert "vendor" in doc.args["query"].lower() or "sdk" in doc.args["query"].lower()


def test_plan_follow_hops_baseline_without_signals() -> None:
    hops = plan_follow_hops(
        project_key="ATLAS",
        question="What is the project name?",
        tool_results=[
            {
                "tool": "project_lookup",
                "result": {"found": True, "status": "active", "key": "ATLAS"},
            },
            {"tool": "risk_list", "result": {"risks": []}},
            {"tool": "blocker_list", "result": {"blockers": []}},
        ],
    )
    tools = {h.tool for h in hops}
    assert tools == {"document_search", "memory_search"}
    assert all("baseline" in h.reason for h in hops)


def test_explain_atlas_hops_helper() -> None:
    explained = explain_atlas_hops(
        [
            {
                "tool": "risk_list",
                "result": {
                    "risks": [{"id": "r1", "title": "Vendor SDK delay", "status": "open"}]
                },
            }
        ]
    )
    assert isinstance(explained, list)
    assert explained[0]["tool"] == "document_search"


@pytest_asyncio.fixture
async def atlas_tools(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SYNAPSE_JWT_SECRET", "integration-test-synapse-jwt-secret!!")
    monkeypatch.setenv("SYNAPSE_ENV", "test")
    for key in (
        "SYNAPSE_TAVILY_API_KEY",
        "TAVILY_API_KEY",
    ):
        monkeypatch.delenv(key, raising=False)
    clear_settings_cache()
    settings = get_settings()
    db = Database(settings.database_dsn())
    await db.connect()
    await create_schema(db.engine)
    sessions = session_factory(db.engine)
    dataset = generate(seed=42, profile="ci")
    async with sessions() as session:
        await load_dataset(session, dataset, password=settings.demo_password)
    admin = next(u for u in dataset.users if u.email == "uma.berg.0@northwind.example")
    ltm = LongTermMemory(sessions)
    await ltm.write(
        tenant_id=admin.tenant_id,
        user_id=admin.id,
        project_key="ATLAS",
        kind="fact",
        content="Prior note: Harbor vendor SDK slip is the top Atlas delivery risk.",
        confidence=0.8,
    )
    tools = ToolSession(
        principal=Principal(
            user_id=admin.id, tenant_id=admin.tenant_id, role=admin.role.value
        ),
        sessions=sessions,
        embedder=None,
        ltm=ltm,
        max_calls=28,
    )
    try:
        yield tools
    finally:
        await db.close()
        clear_settings_cache()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_atlas_multihop_gather_follow_pack(atlas_tools: ToolSession) -> None:
    result = await run_multihop_integration(
        atlas_tools,
        project_key="ATLAS",
        question=ATLAS_CUTOVER_ASK,
    )
    hop_tools = [h["tool"] for h in result.hops]
    assert "email_search" in hop_tools
    assert "meeting_search" in hop_tools
    assert "document_search" in hop_tools
    assert "memory_search" in hop_tools
    assert any(h.get("ok") for h in result.hops if h["tool"] == "email_search")
    assert any(h.get("ok") for h in result.hops if h["tool"] == "meeting_search")

    assert result.pack is not None
    assert result.pack.live_status == "at_risk"
    kinds = {i.source_kind for i in result.pack.items}
    assert SourceKind.LIVE in kinds
    assert SourceKind.LTM in kinds
    assert result.sources_present["live"] is True
    assert result.sources_present["email_or_meeting"] is True
    assert result.sources_present["ltm"] is True
    # Public web (HN / SO). Tavily is a gap here because the fixture clears its key.
    ext_hops = [
        h
        for h in result.hops
        if h["tool"]
        in {
            "hn_search",
            "stackoverflow_search",
            "tavily_search",
        }
    ]
    if any(h.get("ok") for h in ext_hops):
        assert result.sources_present["external"] is True
        assert result.external_providers
        assert SourceKind.EXTERNAL in kinds

    email_items = [i for i in result.pack.items if i.source == "email_search"]
    meeting_items = [i for i in result.pack.items if i.source == "meeting_search"]
    assert email_items, "ATLAS Harbor SDK email must surface via follow hop"
    assert meeting_items, "ATLAS Q2 retro meeting must surface via follow hop"
    assert any(
        "SDK" in (i.summary or "") or "vendor" in (i.summary or "").lower() for i in email_items
    )

    trail_names = [t["tool"] for t in result.tool_trail]
    # gather precedes follow tools
    assert trail_names.index("risk_list") < trail_names.index("email_search")
    assert trail_names.index("blocker_list") < trail_names.index("meeting_search")


@pytest.mark.integration
def test_multihop_http_smoke(monkeypatch: pytest.MonkeyPatch) -> None:
    from starlette.testclient import TestClient

    from synapse.api.app import create_app

    monkeypatch.setenv("SYNAPSE_JWT_SECRET", "integration-test-synapse-jwt-secret!!")
    monkeypatch.setenv("SYNAPSE_ENV", "test")
    clear_settings_cache()
    settings = get_settings()

    async def _seed() -> None:
        db = Database(settings.database_dsn())
        await db.connect()
        try:
            await create_schema(db.engine)
            dataset = generate(seed=42, profile="ci")
            async with session_factory(db.engine)() as session:
                await load_dataset(session, dataset, password=settings.demo_password)
        finally:
            await db.close()

    asyncio.run(_seed())
    app = create_app(settings, connect_stores=True)
    with TestClient(app) as client:
        tok = client.post(
            "/v1/auth/token",
            json={"email": "uma.berg.0@northwind.example", "password": "synapse-demo"},
        )
        assert tok.status_code == 200, tok.text
        token = tok.json()["access_token"]
        r = client.post(
            "/v1/multihop",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "question": "Summarize Q2 Atlas risks",
                "project_key": "ATLAS",
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["project_key"] == "ATLAS"
        assert body["live_status"] == "at_risk"
        assert body["sources_present"]["email_or_meeting"] is True
        assert any(h["tool"] == "email_search" for h in body["hops"])
        assert body["item_count"] >= 3
    clear_settings_cache()
