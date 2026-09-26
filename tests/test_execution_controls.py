"""Execution controls: circuit, admission, deadline, idempotent ask, orphan reaper."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from synapse.agent.graph import AgentBudgets, _chat
from synapse.agent.runner import run_agent
from synapse.auth.principal import Principal
from synapse.platform.config import Settings
from synapse.reliability.admission import AdmissionFull, AskAdmission
from synapse.reliability.circuit import CircuitBreaker, CircuitOpen
from synapse.reliability.deadline import AskDeadlineExceeded
from synapse.tools.runtime import TOOL_TIMEOUT_S, TOOLS, ProjectKeyIn, ToolSession, call_tool


def test_circuit_opens_half_opens_and_closes() -> None:
    now = {"t": 0.0}
    breaker = CircuitBreaker("chat", failure_threshold=2, reset_s=10, clock=lambda: now["t"])
    breaker.before_call()
    breaker.record_failure()
    assert breaker.state == "closed"
    breaker.record_failure()
    assert breaker.state == "open"
    with pytest.raises(CircuitOpen):
        breaker.before_call()
    now["t"] = 10.0
    breaker.before_call()
    assert breaker.state == "half_open"
    with pytest.raises(CircuitOpen):
        breaker.before_call()
    breaker.record_success()
    assert breaker.state == "closed"
    breaker.before_call()


def test_circuit_failure_during_probe_reopens() -> None:
    now = {"t": 100.0}
    breaker = CircuitBreaker("chat", failure_threshold=1, reset_s=30, clock=lambda: now["t"])
    breaker.record_failure()
    now["t"] = 130.0
    breaker.before_call()
    breaker.record_failure()
    assert breaker.state == "open"
    with pytest.raises(CircuitOpen):
        breaker.before_call()


@pytest.mark.asyncio
async def test_chat_does_not_call_provider_when_circuit_is_open() -> None:
    breaker = CircuitBreaker("chat", failure_threshold=1, reset_s=60)
    breaker.record_failure()
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=None)))

    async def _should_not_run(**kwargs: object) -> object:
        raise AssertionError(kwargs)

    client.chat.completions.create = _should_not_run
    with pytest.raises(CircuitOpen):
        await _chat(
            client,  # type: ignore[arg-type]
            "m",
            [{"role": "user", "content": "hello"}],
            breaker=breaker,
        )


@pytest.mark.asyncio
async def test_tenant_cap_leaves_room_for_another_tenant() -> None:
    gate = AskAdmission(max_inflight=4, max_per_tenant=1)
    async with gate.acquire("northwind"):
        with pytest.raises(AdmissionFull) as blocked:
            async with gate.acquire("northwind"):
                pass
        assert blocked.value.scope == "tenant"
        async with gate.acquire("globex"):
            pass


@pytest.mark.asyncio
async def test_process_cap_rejects_without_waiting() -> None:
    gate = AskAdmission(max_inflight=1, max_per_tenant=1)
    async with gate.acquire("northwind"):
        with pytest.raises(AdmissionFull) as blocked:
            async with gate.acquire("globex"):
                pass
        assert blocked.value.scope == "process"


@pytest.mark.asyncio
async def test_run_agent_deadline_cancels_the_graph(monkeypatch: pytest.MonkeyPatch) -> None:
    class SlowGraph:
        async def ainvoke(self, state: object, config: object = None) -> dict[str, object]:
            await asyncio.sleep(2)
            return {}

    monkeypatch.setattr("synapse.agent.runner.get_graph", lambda: SlowGraph())
    monkeypatch.setattr(
        "synapse.agent.runner._client",
        lambda timeout_s: (object(), "test-model"),
    )
    tools = SimpleNamespace(
        max_calls=0,
        call_count=0,
        principal=SimpleNamespace(tenant_id="tnt"),
        embedder=None,
    )
    with pytest.raises(AskDeadlineExceeded):
        await run_agent(
            "what changed on ATLAS",
            tools,  # type: ignore[arg-type]
            budgets=AgentBudgets(deadline_s=0.05, chat_timeout_s=0.01, max_tool_calls=1),
        )


def test_pool_must_cover_inflight_asks() -> None:
    with pytest.raises(ValidationError, match="pool"):
        Settings(
            _env_file=None,  # type: ignore[call-arg]
            database_url="postgresql+asyncpg://u:p@localhost:5432/synapse",
            redis_url="redis://localhost:6379/0",
            jwt_secret="dev-only-change-me-synapse-jwt-32chars!!",
            ask_max_inflight=20,
            ask_max_inflight_per_tenant=2,
            db_pool_size=5,
            db_max_overflow=3,
        )


@pytest.mark.asyncio
async def test_idempotent_claim_replays_and_blocks_inflight() -> None:
    from synapse.data.generate import generate
    from synapse.memory.stm import ShortTermMemory
    from synapse.platform.config import clear_settings_cache, get_settings
    from synapse.platform.db import Database
    from synapse.platform.seed import create_schema, load_dataset, session_factory

    clear_settings_cache()
    settings = get_settings()
    db = Database(settings.database_dsn())
    await db.connect()
    try:
        await create_schema(db.engine)
        sessions = session_factory(db.engine)
        async with sessions() as session:
            await load_dataset(
                session, generate(seed=42, profile="ci"), password=settings.demo_password
            )
        stm = ShortTermMemory(sessions)
        question = "What is blocking ATLAS right now?"
        first = await stm.claim_run(
            tenant_id="tnt_nw",
            user_id="usr_idem",
            project_key="ATLAS",
            question=question,
            idempotency_key="idem-key-001",
        )
        assert first.action == "execute"
        second = await stm.claim_run(
            tenant_id="tnt_nw",
            user_id="usr_idem",
            project_key="ATLAS",
            question=question,
            idempotency_key="idem-key-001",
        )
        assert second.action == "in_progress"
        assert second.run_id == first.run_id
        await stm.fail(first.run_id, tenant_id="tnt_nw", error="boom")
        retried = await stm.claim_run(
            tenant_id="tnt_nw",
            user_id="usr_idem",
            project_key="ATLAS",
            question=question,
            idempotency_key="idem-key-001",
        )
        assert retried.action == "execute"
        assert retried.run_id == first.run_id
        await stm.complete(
            first.run_id,
            tenant_id="tnt_nw",
            answer="vendor blocker",
            citations=[],
            gaps=[],
            conflicts=[],
            plan=["live_status"],
            checklist={},
            tool_trail=[],
            usage={},
        )
        await stm.save_response(
            first.run_id,
            tenant_id="tnt_nw",
            response={"run_id": first.run_id, "answer": "vendor blocker"},
        )
        replay = await stm.claim_run(
            tenant_id="tnt_nw",
            user_id="usr_idem",
            project_key="ATLAS",
            question=question,
            idempotency_key="idem-key-001",
        )
        assert replay.action == "replay"
        assert replay.response is not None
        assert replay.response["answer"] == "vendor blocker"
        assert replay.response["replayed"] is True
        mismatch = await stm.claim_run(
            tenant_id="tnt_nw",
            user_id="usr_idem",
            project_key="HARBOR",
            question=question,
            idempotency_key="idem-key-001",
        )
        assert mismatch.action == "mismatch"
    finally:
        await db.close()
        clear_settings_cache()


@pytest.mark.asyncio
async def test_startup_reaper_fails_running_asks() -> None:
    from synapse.memory.stm import ShortTermMemory
    from synapse.platform.config import clear_settings_cache, get_settings
    from synapse.platform.db import Database
    from synapse.platform.seed import create_schema, session_factory

    clear_settings_cache()
    settings = get_settings()
    db = Database(settings.database_dsn())
    await db.connect()
    try:
        await create_schema(db.engine)
        sessions = session_factory(db.engine)
        stm = ShortTermMemory(sessions)
        run_id = await stm.start_run(
            tenant_id="tnt_nw",
            user_id="usr_orphan",
            project_key="ATLAS",
            question="left running after a crash",
        )
        failed = await stm.fail_orphaned_runs()
        assert failed >= 1
        run = await stm.get_run(tenant_id="tnt_nw", run_id=run_id)
        assert run is not None
        assert run.status == "failed"
        assert run.error == "orphaned_on_restart"
    finally:
        await db.close()
        clear_settings_cache()


def test_ask_idempotency_key_replays_over_http(
    seeded_client: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    from synapse.agent.graph import AgentResult
    from tests.conftest import auth_token

    async def fake(question: str, tools: object, **kwargs: object) -> AgentResult:
        stm = kwargs["stm"]
        run_id = kwargs["run_id"]
        tenant_id = tools.principal.tenant_id  # type: ignore[attr-defined]
        await stm.complete(  # type: ignore[attr-defined]
            str(run_id),
            tenant_id=tenant_id,
            answer="live status stands",
            citations=[],
            gaps=[],
            conflicts=[],
            plan=["live_status"],
            checklist={},
            tool_trail=[],
            usage={},
        )
        return AgentResult(
            answer="live status stands",
            citations=[],
            gaps=[],
            tool_trail=[],
            plan=["live_status"],
        )

    monkeypatch.setattr("synapse.api.ask_routes.run_agent", fake)
    token = auth_token(seeded_client, "uma.berg.0@northwind.example")
    headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": "http-idem-key-01"}
    body = {"question": "What is the current ATLAS status?", "project_key": "ATLAS"}
    first = seeded_client.post("/v1/ask", json=body, headers=headers)
    assert first.status_code == 200, first.text
    second = seeded_client.post("/v1/ask", json=body, headers=headers)
    assert second.status_code == 200, second.text
    assert second.headers.get("idempotent-replayed") == "true"
    assert second.json()["replayed"] is True
    assert second.json()["answer"] == "live status stands"
    reused = seeded_client.post(
        "/v1/ask",
        json={"question": "Which open risks on ATLAS look critical?", "project_key": "ATLAS"},
        headers=headers,
    )
    assert reused.status_code == 422
    bad = seeded_client.post(
        "/v1/ask",
        json=body,
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "short"},
    )
    assert bad.status_code == 400


@pytest.mark.asyncio
async def test_tool_timeout_returns_gap(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _slow(_session: ToolSession, _args: ProjectKeyIn) -> dict[str, str]:
        await asyncio.sleep(1)
        return {"found": True}

    monkeypatch.setitem(TOOLS, "project_lookup", (ProjectKeyIn, _slow))
    monkeypatch.setitem(TOOL_TIMEOUT_S, "project_lookup", 0.01)
    session = ToolSession(
        principal=Principal(user_id="u", tenant_id="tnt_nw", role="admin"),
        sessions=None,  # type: ignore[arg-type]
    )
    result = await call_tool(session, "project_lookup", {"project_key": "ATLAS"})
    assert result["error"] == "tool_timeout"
    assert result["tool"] == "project_lookup"
