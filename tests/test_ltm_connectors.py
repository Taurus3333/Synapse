"""LTM unit/integration tests + connector unavailable behaviour."""

from __future__ import annotations

import pytest
import pytest_asyncio

from synapse.evidence.assemble import assemble_pack
from synapse.evidence.types import SourceKind
from synapse.memory.ltm import LongTermMemory
from synapse.platform.config import clear_settings_cache, get_settings
from synapse.platform.db import Database
from synapse.platform.seed import create_schema, session_factory
from synapse.tools import connectors


@pytest_asyncio.fixture
async def ltm(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SYNAPSE_JWT_SECRET", "integration-test-synapse-jwt-secret!!")
    monkeypatch.setenv("SYNAPSE_ENV", "test")
    clear_settings_cache()
    settings = get_settings()
    db = Database(settings.database_dsn())
    await db.connect()
    await create_schema(db.engine)
    memory = LongTermMemory(session_factory(db.engine))
    try:
        yield memory
    finally:
        await db.close()
        clear_settings_cache()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_ltm_write_search_supersede_and_tenant(ltm: LongTermMemory) -> None:
    mid = await ltm.write(
        tenant_id="tnt_nw",
        content="Vendor SDK slip delayed ATLAS Q2 cutover.",
        kind="fact",
        project_key="ATLAS",
        confidence=0.7,
    )
    hits = await ltm.search(tenant_id="tnt_nw", query="SDK ATLAS", project_key="ATLAS")
    assert any(h["id"] == mid for h in hits)
    assert await ltm.search(tenant_id="tnt_gx", query="SDK ATLAS") == []

    mid2 = await ltm.write(
        tenant_id="tnt_nw",
        content="Vendor SDK delivered; cutover unblocked.",
        kind="fact",
        project_key="ATLAS",
        supersedes=mid,
    )
    hits2 = await ltm.search(tenant_id="tnt_nw", query="SDK", project_key="ATLAS")
    ids = {h["id"] for h in hits2}
    assert mid2 in ids
    assert mid not in ids


@pytest.mark.asyncio
async def test_connectors_public_without_personal_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "SYNAPSE_GITHUB_TOKEN",
        "GITHUB_TOKEN",
        "SYNAPSE_SLACK_BOT_TOKEN",
        "SLACK_BOT_TOKEN",
        "SYNAPSE_GMAIL_ACCESS_TOKEN",
        "GMAIL_ACCESS_TOKEN",
    ):
        monkeypatch.delenv(key, raising=False)
    status = connectors.connector_status()
    assert status["live_db"]["ready"] is True
    assert status["public_external"]["github"]["ready"] is True
    assert status["public_external"]["wikipedia"]["ready"] is True
    assert status["optional_private"]["slack"]["provider"] == "hackernews"
    assert status["optional_private"]["gmail"]["provider"] == "stackoverflow"
    assert status["github"]["authenticated"] is False
    assert status["slack"]["authenticated"] is False
    assert status["gmail"]["authenticated"] is False


def test_assemble_external_and_ltm_precedence() -> None:
    pack = assemble_pack(
        project_key="ATLAS",
        tool_results=[
            {
                "tool": "project_lookup",
                "result": {
                    "found": True,
                    "id": "prj_1",
                    "key": "ATLAS",
                    "status": "at_risk",
                    "priority": "high",
                },
            },
            {
                "tool": "github_search",
                "result": {
                    "hits": [
                        {
                            "id": "gh_1",
                            "text": "SDK delay issue",
                            "framed": "<<<DATA>>>",
                        }
                    ]
                },
            },
            {
                "tool": "memory_search",
                "result": {
                    "memories": [
                        {"id": "mem_1", "content": "Prior note about SDK", "kind": "summary"}
                    ]
                },
            },
        ],
    )
    kinds = {i.record_id: i for i in pack.items}
    assert kinds["prj_1"].source_kind == SourceKind.LIVE
    assert kinds["prj_1"].precedence == 0
    assert kinds["gh_1"].source_kind == SourceKind.EXTERNAL
    assert kinds["gh_1"].precedence == 5
    assert kinds["mem_1"].source_kind == SourceKind.LTM
    assert kinds["mem_1"].precedence == 20
