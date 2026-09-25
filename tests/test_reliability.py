"""Chunk 13 — retries, classification, DLQ-lite."""

from __future__ import annotations

import pytest

from synapse.reliability.http import HttpStatusError
from synapse.reliability.retry import (
    RetryPolicy,
    is_retryable_exception,
    is_retryable_http_status,
    with_retry,
)


def test_retryable_status_codes() -> None:
    assert is_retryable_http_status(429)
    assert is_retryable_http_status(503)
    assert not is_retryable_http_status(400)
    assert not is_retryable_http_status(404)


def test_retryable_exception_names() -> None:
    class TimeoutException(Exception):
        pass

    assert is_retryable_exception(TimeoutException())
    assert not is_retryable_exception(ValueError("nope"))
    assert is_retryable_exception(HttpStatusError(503, "busy"))


@pytest.mark.asyncio
async def test_with_retry_succeeds_after_transient() -> None:
    calls = {"n": 0}

    async def flaky() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise HttpStatusError(503, "try again")
        return "ok"

    out = await with_retry(flaky, policy=RetryPolicy(attempts=4, base_delay_s=0.01, jitter=0))
    assert out == "ok"
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_with_retry_does_not_retry_client_errors() -> None:
    calls = {"n": 0}

    async def bad() -> str:
        calls["n"] += 1
        raise HttpStatusError(400, "nope")

    with pytest.raises(HttpStatusError):
        await with_retry(bad, policy=RetryPolicy(attempts=3, base_delay_s=0.01, jitter=0))
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_dlq_list_and_ack(monkeypatch: pytest.MonkeyPatch) -> None:
    from synapse.data.generate import generate
    from synapse.platform.config import clear_settings_cache, get_settings
    from synapse.platform.db import Database
    from synapse.platform.seed import create_schema, load_dataset, session_factory
    from synapse.reliability.dlq import DeadLetterLite
    from synapse.memory.stm import ShortTermMemory

    monkeypatch.setenv("SYNAPSE_JWT_SECRET", "integration-test-synapse-jwt-secret!!")
    monkeypatch.setenv("SYNAPSE_ENV", "test")
    clear_settings_cache()
    settings = get_settings()
    db = Database(settings.database_dsn())
    await db.connect()
    await create_schema(db.engine)
    sessions = session_factory(db.engine)
    async with sessions() as session:
        await load_dataset(session, generate(seed=42, profile="ci"), password=settings.demo_password)

    stm = ShortTermMemory(sessions)
    run_id = await stm.start_run(
        tenant_id="tnt_nw",
        user_id="usr_test",
        project_key="ATLAS",
        question="force fail path",
    )
    await stm.fail(run_id, tenant_id="tnt_nw", error="boom")

    dlq = DeadLetterLite(sessions)
    items = await dlq.list_failed(tenant_id="tnt_nw")
    assert any(i["id"] == run_id for i in items)
    assert await dlq.acknowledge(tenant_id="tnt_nw", run_id=run_id)
    items2 = await dlq.list_failed(tenant_id="tnt_nw")
    assert not any(i["id"] == run_id for i in items2)
    items3 = await dlq.list_failed(tenant_id="tnt_nw", include_acked=True)
    assert any(i["id"] == run_id and i["dlq_acked"] for i in items3)

    await db.close()
    clear_settings_cache()
