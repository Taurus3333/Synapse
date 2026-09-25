"""STM durable run + checkpoint tests (no LLM)."""

from __future__ import annotations

import pytest
import pytest_asyncio

from synapse.memory.stm import ShortTermMemory
from synapse.platform.config import clear_settings_cache, get_settings
from synapse.platform.db import Database
from synapse.platform.seed import create_schema, session_factory


@pytest_asyncio.fixture
async def stm(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SYNAPSE_JWT_SECRET", "integration-test-synapse-jwt-secret!!")
    monkeypatch.setenv("SYNAPSE_ENV", "test")
    clear_settings_cache()
    settings = get_settings()
    db = Database(settings.database_dsn())
    await db.connect()
    await create_schema(db.engine)
    memory = ShortTermMemory(session_factory(db.engine))
    try:
        yield memory
    finally:
        await db.close()
        clear_settings_cache()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_run_checkpoint_complete_and_tenant_isolation(stm: ShortTermMemory) -> None:
    run_id = await stm.start_run(
        tenant_id="tnt_nw",
        user_id="usr_nw_00001",
        project_key="ATLAS",
        question="What changed in Q2?",
    )
    assert run_id.startswith("run_")
    step = await stm.checkpoint(
        run_id,
        tenant_id="tnt_nw",
        phase="plan",
        payload={"plan": ["project_baseline", "risk_records"]},
    )
    assert step >= 1
    await stm.complete(
        run_id,
        tenant_id="tnt_nw",
        answer="Vendor SDK risk.",
        citations=[{"id": "rsk_1", "source": "risk_list"}],
        gaps=[],
        conflicts=[],
        plan=["project_baseline"],
        checklist={"project_baseline": "filled"},
        tool_trail=[{"tool": "project_lookup", "ok": True}],
        usage={"tool_calls": 1},
    )
    run = await stm.get_run(tenant_id="tnt_nw", run_id=run_id)
    assert run is not None
    assert run.status == "completed"
    assert run.phase == "done"
    assert run.answer and "SDK" in run.answer
    ckpts = await stm.list_checkpoints(tenant_id="tnt_nw", run_id=run_id)
    phases = [c.phase for c in ckpts]
    assert "started" in phases and "plan" in phases and "done" in phases

    assert await stm.get_run(tenant_id="tnt_gx", run_id=run_id) is None
    assert await stm.list_checkpoints(tenant_id="tnt_gx", run_id=run_id) == []


@pytest.mark.integration
@pytest.mark.asyncio
async def test_fail_records_error(stm: ShortTermMemory) -> None:
    run_id = await stm.start_run(
        tenant_id="tnt_nw",
        user_id="usr_nw_00001",
        project_key="ATLAS",
        question="boom",
    )
    await stm.fail(run_id, tenant_id="tnt_nw", error="provider down")
    run = await stm.get_run(tenant_id="tnt_nw", run_id=run_id)
    assert run is not None
    assert run.status == "failed"
    assert run.error == "provider down"
