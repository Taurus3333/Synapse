"""Live web connectors: Hacker News, Stack Overflow, and Tavily."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from synapse.tools import connectors


def test_scrub_query_drops_synthetic_project_keys() -> None:
    q = connectors.scrub_query("ATLAS vendor SDK freeze Harbor")
    low = q.lower()
    assert "atlas" not in low
    assert "harbor" not in low
    assert "vendor" in low or "sdk" in low or "freeze" in low


def test_connector_status_public_ready_without_tavily_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SYNAPSE_TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    status = connectors.connector_status()
    assert status["live_db"]["ready"] is True
    assert status["live_db"]["mode"] == "sql"
    assert "project_lookup" in status["live_db"]["tools"]
    assert status["public_external"]["hackernews"]["ready"] is True
    assert status["public_external"]["stackoverflow"]["ready"] is True
    assert status["public_external"]["tavily"]["ready"] is False
    assert status["public_external"]["tavily"]["mode"] == "missing_key"
    assert "github" not in status["public_external"]
    assert "optional_private" not in status


@pytest.mark.asyncio
async def test_tavily_missing_key_is_a_gap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SYNAPSE_TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    result = await connectors.tavily_search(query="ATLAS vendor SDK", limit=2)
    assert result["provider"] == "tavily"
    assert result["unavailable"] is True
    assert result["error"] == "tavily_unavailable"
    assert result["hits"] == []


@pytest.mark.asyncio
async def test_tavily_search_frames_hits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYNAPSE_TAVILY_API_KEY", "test-key-not-sent-to-logs")
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = {
        "results": [
            {
                "url": "https://example.com/sdk-cutover",
                "title": "Vendor SDK cutover delay",
                "content": "Teams report a freeze while the SDK ships.",
                "score": 0.8,
            }
        ]
    }
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=fake_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("synapse.tools.connectors.httpx.AsyncClient", return_value=mock_client):
        result = await connectors.tavily_search(query="ATLAS vendor SDK freeze", limit=2)

    assert result["provider"] == "tavily"
    assert result["mode"] == "authenticated"
    assert result["hits"]
    assert "ATLAS" not in result["query"]
    assert "SDK" in result["hits"][0]["text"]
    assert result["hits"][0]["framed"]
    sent = mock_client.post.await_args
    assert sent.args[0] == "https://api.tavily.com/search"
    assert "api_key" not in sent.kwargs["json"]


@pytest.mark.asyncio
async def test_hackernews_search_public() -> None:
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = {
        "hits": [
            {
                "objectID": "99",
                "title": "Production outage postmortem",
                "story_text": "vendor miss",
                "created_at": "2026-01-02T00:00:00.000Z",
                "points": 10,
                "url": "https://example.com",
            }
        ]
    }
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=fake_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("synapse.tools.connectors.httpx.AsyncClient", return_value=mock_client):
        result = await connectors.hackernews_search(query="ATLAS outage", limit=2)

    assert result["provider"] == "hackernews"
    assert result["mode"] == "public"
    assert result["hits"][0]["id"] == "hn_99"


@pytest.mark.asyncio
async def test_stackoverflow_search_public() -> None:
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = {
        "items": [
            {
                "question_id": 7,
                "title": "SDK integration failing in CI",
                "tags": ["sdk"],
                "score": 12,
                "is_answered": True,
                "link": "https://stackoverflow.com/q/7",
                "creation_date": 1700000000,
            }
        ]
    }
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=fake_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("synapse.tools.connectors.httpx.AsyncClient", return_value=mock_client):
        result = await connectors.stackoverflow_search(query="ATLAS SDK", limit=2)

    assert result["provider"] == "stackoverflow"
    assert result["mode"] == "public"
    assert result["hits"][0]["id"] == "so_7"
    assert "ATLAS" not in result["query"]


@pytest.mark.asyncio
@pytest.mark.integration
async def test_live_public_connectors_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Real HTTP for HN and Stack Overflow. Tavily is skipped without a key."""
    monkeypatch.delenv("SYNAPSE_TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    hn = await connectors.hackernews_search(query="sdk", limit=2)
    if hn.get("error"):
        pytest.skip(f"hn unreachable: {hn.get('error')}")
    assert hn["hits"]
    assert hn["provider"] == "hackernews"

    so = await connectors.stackoverflow_search(query="sdk deployment", limit=2)
    if so.get("error"):
        pytest.skip(f"so unreachable: {so.get('error')}")
    assert so["hits"]

    missing = await connectors.tavily_search(query="vendor SDK", limit=1)
    assert missing["unavailable"] is True

    probe = await connectors.probe_connectors()
    assert "probes" in probe
    assert probe["probes"]["live_db"]["ok"] is True
    assert probe["probes"]["tavily"]["mode"] == "missing_key"
    assert probe["providers"]["public_external"]["hackernews"]["ready"] is True
    assert probe["providers"]["public_external"]["stackoverflow"]["ready"] is True
