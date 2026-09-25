"""Public live connectors — no personal Gmail/empty-repo GitHub required."""

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


def test_connector_status_public_ready_without_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "SYNAPSE_GITHUB_TOKEN",
        "GITHUB_TOKEN",
        "SYNAPSE_SLACK_BOT_TOKEN",
        "SLACK_BOT_TOKEN",
        "SYNAPSE_GMAIL_ACCESS_TOKEN",
        "GMAIL_ACCESS_TOKEN",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("SYNAPSE_GITHUB_PUBLIC_REPO", "kubernetes/kubernetes")
    status = connectors.connector_status()
    assert status["live_db"]["ready"] is True
    assert status["live_db"]["mode"] == "sql"
    assert "project_lookup" in status["live_db"]["tools"]
    assert status["public_external"]["github"]["ready"] is True
    assert status["public_external"]["github"]["mode"] == "public"
    assert status["public_external"]["github"]["target"] == "kubernetes/kubernetes"
    assert status["public_external"]["hackernews"]["ready"] is True
    assert status["public_external"]["stackoverflow"]["ready"] is True
    assert status["public_external"]["wikipedia"]["ready"] is True
    assert status["optional_private"]["slack"]["provider"] == "hackernews"
    assert status["optional_private"]["gmail"]["provider"] == "stackoverflow"


@pytest.mark.asyncio
async def test_wikipedia_search_public(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_open = MagicMock()
    fake_open.status_code = 200
    fake_open.json.return_value = [
        "sdk",
        ["Software development kit"],
        ["A set of tools"],
        ["https://en.wikipedia.org/wiki/Software_development_kit"],
    ]
    fake_extract = MagicMock()
    fake_extract.status_code = 200
    fake_extract.json.return_value = {
        "query": {"pages": {"1": {"extract": "An SDK is a collection of tools."}}}
    }
    mock_client = AsyncMock()
    mock_client.get = AsyncMock(side_effect=[fake_open, fake_extract])
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("synapse.tools.connectors.httpx.AsyncClient", return_value=mock_client):
        result = await connectors.wikipedia_search(query="ATLAS SDK", limit=2)

    assert result["provider"] == "wikipedia"
    assert result["hits"]
    assert "SDK" in result["hits"][0]["text"] or "tools" in result["hits"][0]["text"]


def test_public_github_repo_rejects_junk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYNAPSE_GITHUB_PUBLIC_REPO", "not a repo!!!")
    assert connectors.public_github_repo() == "kubernetes/kubernetes"


@pytest.mark.asyncio
async def test_github_search_public_uses_repo_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SYNAPSE_GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("SYNAPSE_GITHUB_PUBLIC_REPO", "kubernetes/kubernetes")

    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = {
        "items": [
            {
                "id": 42,
                "title": "SDK flake in e2e",
                "body": "vendor delay",
                "html_url": "https://github.com/kubernetes/kubernetes/issues/42",
                "state": "open",
                "updated_at": "2026-01-01T00:00:00Z",
            }
        ]
    }

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=fake_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)

    with patch("synapse.tools.connectors.httpx.AsyncClient", return_value=mock_client):
        result = await connectors.github_search(query="ATLAS vendor SDK", limit=3)

    assert result.get("unavailable") is None
    assert result["provider"] == "github"
    assert result["mode"] == "public"
    assert result["target"] == "kubernetes/kubernetes"
    assert result["hits"]
    assert "repo:kubernetes/kubernetes" in result["query"]
    assert "is:issue" in result["query"]
    assert "ATLAS" not in result["query"]
    # Query uses OR so multi-token ATLAS titles still recall
    assert " OR " in result["query"] or "vendor" in result["query"].lower()
    call_args = mock_client.get.await_args
    assert call_args.args[0] == "https://api.github.com/search/issues"


@pytest.mark.asyncio
async def test_slack_falls_back_to_hackernews(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SYNAPSE_SLACK_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)

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
        result = await connectors.slack_search(query="ATLAS outage", limit=2)

    assert result["provider"] == "hackernews"
    assert result["mode"] == "public"
    assert result["hits"][0]["id"] == "hn_99"


@pytest.mark.asyncio
async def test_gmail_falls_back_to_stackoverflow(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SYNAPSE_GMAIL_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("GMAIL_ACCESS_TOKEN", raising=False)

    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = {
        "items": [
            {
                "question_id": 7,
                "title": "SDK integration failing in CI",
                "tags": ["kubernetes", "sdk"],
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
        result = await connectors.gmail_search(query="ATLAS SDK", limit=2)

    assert result["provider"] == "stackoverflow"
    assert result["mode"] == "public"
    assert result["hits"][0]["id"] == "so_7"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_live_public_connectors_network() -> None:
    """Real HTTP — skips soft if offline / rate-limited."""
    gh = await connectors.github_search(query="scheduler latency", limit=2)
    if gh.get("error"):
        pytest.skip(f"github unreachable: {gh.get('error')}")
    assert gh["hits"], "expected at least one live kubernetes issue"
    assert gh["provider"] == "github"

    hn = await connectors.hackernews_search(query="kubernetes", limit=2)
    if hn.get("error"):
        pytest.skip(f"hn unreachable: {hn.get('error')}")
    assert hn["hits"]

    so = await connectors.stackoverflow_search(query="kubernetes deployment", limit=2)
    if so.get("error"):
        pytest.skip(f"so unreachable: {so.get('error')}")
    assert so["hits"]

    wiki = await connectors.wikipedia_search(query="kubernetes", limit=2)
    if wiki.get("error"):
        pytest.skip(f"wiki unreachable: {wiki.get('error')}")
    assert wiki["hits"]

    probe = await connectors.probe_connectors()
    assert "probes" in probe
    assert probe["probes"]["live_db"]["ok"] is True
    assert probe["providers"]["live_db"]["ready"] is True
    assert probe["providers"]["public_external"]["wikipedia"]["ready"] is True
