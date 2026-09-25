"""Shared fixtures for integration / e2e HTTP tests."""

from __future__ import annotations

import asyncio

import pytest
from starlette.testclient import TestClient

from synapse.api.app import create_app
from synapse.data.generate import generate
from synapse.platform.config import clear_settings_cache, get_settings
from synapse.platform.db import Database
from synapse.platform.seed import create_schema, load_dataset, session_factory


@pytest.fixture
def seeded_client(monkeypatch: pytest.MonkeyPatch):
    """Seeded FastAPI TestClient (Postgres + Redis required)."""
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
    with TestClient(app) as ac:
        yield ac
    clear_settings_cache()


def auth_token(client: TestClient, email: str, password: str = "synapse-demo") -> str:
    r = client.post("/v1/auth/token", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]
