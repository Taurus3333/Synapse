"""HTTP auth + tenant isolation against a live seeded DB."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from synapse.api.app import create_app
from synapse.data.generate import generate
from synapse.platform.config import clear_settings_cache, get_settings
from synapse.platform.db import Database
from synapse.platform.seed import create_schema, load_dataset, session_factory


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SYNAPSE_JWT_SECRET", "integration-test-synapse-jwt-secret!!")
    monkeypatch.setenv("SYNAPSE_ENV", "test")
    clear_settings_cache()
    settings = get_settings()

    import asyncio

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


def _token(client: TestClient, email: str, password: str = "synapse-demo") -> str:
    r = client.post("/v1/auth/token", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


@pytest.mark.integration
def test_token_and_me(client: TestClient) -> None:
    token = _token(client, "uma.berg.0@northwind.example")
    r = client.get("/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert body["tenant_id"] == "tnt_nw"
    assert body["role"] == "admin"


@pytest.mark.integration
def test_cross_tenant_project_is_404(client: TestClient) -> None:
    ds = generate(seed=42, profile="ci")
    globex = next(u for u in ds.users if u.tenant_id == "tnt_gx")
    token = _token(client, globex.email)
    r = client.get("/v1/projects/ATLAS", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 404


@pytest.mark.integration
def test_spoofed_tenant_header_rejected(client: TestClient) -> None:
    token = _token(client, "uma.berg.0@northwind.example")
    r = client.get(
        "/v1/projects",
        headers={"Authorization": f"Bearer {token}", "X-Tenant-ID": "tnt_gx"},
    )
    assert r.status_code == 403


@pytest.mark.integration
def test_viewer_cannot_mutate(client: TestClient) -> None:
    token = _token(client, "rosa.nguyen.4@northwind.example")
    r = client.patch(
        "/v1/projects/ATLAS/status",
        headers={"Authorization": f"Bearer {token}"},
        json={"status": "active"},
    )
    assert r.status_code == 403


@pytest.mark.integration
def test_unauthenticated_rejected(client: TestClient) -> None:
    assert client.get("/v1/projects").status_code == 401
