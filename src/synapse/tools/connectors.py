"""Live external connectors beside the seeded Postgres graph.

Three public sources, fetched at ask time (not stored as a dataset):
  - hn_search              → Hacker News Algolia (no key)
  - stackoverflow_search   → Stack Exchange API (key optional)
  - tavily_search          → Tavily web search (SYNAPSE_TAVILY_API_KEY)

Missing Tavily key returns a gap. It does not crash the ask.
All retrieved text is framed as DATA, not instructions.
"""

from __future__ import annotations

import hashlib
import os
import re
from typing import Any

import httpx

from synapse.evidence.frame import frame_retrieved_data
from synapse.reliability.http import HttpStatusError
from synapse.reliability.retry import RetryPolicy, is_retryable_http_status, with_retry

# Synthetic project keys — strip them so "ATLAS SDK" still searches the public web.
_SYNTH_KEYS = frozenset(
    {"atlas", "harbor", "quay", "beacon", "northwind", "globex", "initech"}
)

_USER_AGENT = "Synapse/1.0 (+local demo)"
_HTTP_RETRY = RetryPolicy(attempts=3, base_delay_s=0.2, max_delay_s=2.0)


async def _resilient_get(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """Idempotent GET with backoff on 429/5xx and transport blips."""

    async def _once() -> httpx.Response:
        r = await client.get(url, params=params, headers=headers)
        if is_retryable_http_status(r.status_code):
            raise HttpStatusError(r.status_code, r.text)
        return r

    return await with_retry(_once, policy=_HTTP_RETRY)


def _secret(*names: str) -> str | None:
    for name in names:
        val = os.environ.get(name, "").strip()
        if val:
            return val
    return None


def scrub_query(query: str, *, fallback: str = "risk OR blocker OR delay OR SDK") -> str:
    """Drop synthetic tenant/project tokens; keep a short real-world search string."""
    parts: list[str] = []
    for tok in re.findall(r"[A-Za-z][A-Za-z0-9_./-]{1,}", query):
        low = tok.lower()
        if low in _SYNTH_KEYS:
            continue
        # Skip low-signal verbs/nouns that poison AND-style web search
        if low in {
            "miss",
            "threatens",
            "threaten",
            "feature-flag",
            "feature",
            "flag",
            "slice",
            "action",
            "owner",
            "update",
            "register",
        }:
            continue
        if tok not in parts:
            parts.append(tok)
        if len(parts) >= 4:
            break
    return " ".join(parts) if parts else fallback


def connector_status() -> dict[str, Any]:
    """Catalog: seeded Postgres (live rows) + HN, Stack Overflow, and Tavily."""
    tavily_key = bool(_secret("SYNAPSE_TAVILY_API_KEY", "TAVILY_API_KEY"))
    return {
        "live_db": {
            "ready": True,
            "mode": "sql",
            "provider": "postgres",
            "note": "Tenant-scoped enterprise rows queried at ask time — not embedded.",
            "tools": [
                "project_lookup",
                "risk_list",
                "blocker_list",
                "task_search",
                "project_activity",
                "email_search",
                "meeting_search",
            ],
        },
        "public_external": {
            "hackernews": {
                "ready": True,
                "mode": "public",
                "provider": "hackernews",
                "tool": "hn_search",
            },
            "stackoverflow": {
                "ready": True,
                "mode": "public",
                "provider": "stackoverflow",
                "tool": "stackoverflow_search",
            },
            "tavily": {
                "ready": tavily_key,
                "mode": "authenticated" if tavily_key else "missing_key",
                "provider": "tavily",
                "authenticated": tavily_key,
                "tool": "tavily_search",
            },
        },
        "hackernews": {"ready": True, "mode": "public", "provider": "hackernews"},
        "stackoverflow": {"ready": True, "mode": "public", "provider": "stackoverflow"},
        "tavily": {
            "ready": tavily_key,
            "mode": "authenticated" if tavily_key else "missing_key",
            "provider": "tavily",
            "authenticated": tavily_key,
        },
    }


async def probe_connectors() -> dict[str, Any]:
    """Liveness for HN and Stack Overflow. Tavily is probed only when a key is set."""
    out: dict[str, Any] = {
        "live_db": {"ok": True, "provider": "postgres", "mode": "sql"},
    }
    async with httpx.AsyncClient(timeout=12.0, headers={"User-Agent": _USER_AGENT}) as client:
        out["hackernews"] = await _probe_hn(client)
        out["stackoverflow"] = await _probe_so(client)
        out["tavily"] = await _probe_tavily(client)
    status = connector_status()
    required = [out["live_db"], out["hackernews"], out["stackoverflow"]]
    if out["tavily"].get("mode") == "authenticated":
        required.append(out["tavily"])
    ready = all(v.get("ok") for v in required)
    return {"ready": ready, "probes": out, "providers": status}


async def _probe_hn(client: httpx.AsyncClient) -> dict[str, Any]:
    try:
        r = await client.get(
            "https://hn.algolia.com/api/v1/search",
            params={"query": "sdk", "hitsPerPage": 1, "tags": "story"},
        )
        return {"ok": r.status_code == 200, "status_code": r.status_code, "provider": "hackernews"}
    except httpx.HTTPError as exc:
        return {"ok": False, "error": type(exc).__name__, "provider": "hackernews"}


async def _probe_so(client: httpx.AsyncClient) -> dict[str, Any]:
    try:
        r = await client.get(
            "https://api.stackexchange.com/2.3/search/advanced",
            params={
                "order": "desc",
                "sort": "relevance",
                "q": "sdk",
                "site": "stackoverflow",
                "pagesize": 1,
            },
        )
        return {
            "ok": r.status_code == 200,
            "status_code": r.status_code,
            "provider": "stackoverflow",
        }
    except httpx.HTTPError as exc:
        return {"ok": False, "error": type(exc).__name__, "provider": "stackoverflow"}


async def _probe_tavily(client: httpx.AsyncClient) -> dict[str, Any]:
    key = _secret("SYNAPSE_TAVILY_API_KEY", "TAVILY_API_KEY")
    if not key:
        return {"ok": False, "provider": "tavily", "mode": "missing_key"}
    try:
        r = await client.post(
            "https://api.tavily.com/search",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json={
                "query": "vendor SDK",
                "max_results": 1,
                "search_depth": "basic",
                "include_answer": False,
            },
        )
        return {
            "ok": r.status_code == 200,
            "status_code": r.status_code,
            "provider": "tavily",
            "mode": "authenticated",
        }
    except httpx.HTTPError as exc:
        return {
            "ok": False,
            "error": type(exc).__name__,
            "provider": "tavily",
            "mode": "authenticated",
        }


async def hackernews_search(*, query: str, limit: int = 5) -> dict[str, Any]:
    """Public HN Algolia — live community signal, no API key."""
    cleaned = scrub_query(query, fallback="vendor SDK production outage")
    # HN relevance collapses on long rare phrases — keep it short.
    short = " ".join(cleaned.split()[:3]) or cleaned
    try:
        async with httpx.AsyncClient(timeout=15.0, headers={"User-Agent": _USER_AGENT}) as client:
            r = await _resilient_get(
                client,
                "https://hn.algolia.com/api/v1/search",
                params={
                    "query": short,
                    "hitsPerPage": min(limit, 10),
                    "tags": "(story,comment)",
                },
            )
            if r.status_code >= 400:
                return {
                    "error": f"hn_http_{r.status_code}",
                    "hits": [],
                    "provider": "hackernews",
                    "mode": "public",
                }
            rows = r.json().get("hits") or []
            if not rows:
                r = await _resilient_get(
                    client,
                    "https://hn.algolia.com/api/v1/search",
                    params={"query": "sdk", "hitsPerPage": min(limit, 10), "tags": "story"},
                )
                rows = (r.json().get("hits") or []) if r.status_code < 400 else []
    except (httpx.HTTPError, HttpStatusError) as exc:
        return {
            "error": f"hn_network_{type(exc).__name__}",
            "hits": [],
            "provider": "hackernews",
            "mode": "public",
        }

    hits = []
    for row in rows[:limit]:
        oid = row.get("objectID") or row.get("story_id") or "0"
        rid = f"hn_{oid}"
        title = row.get("title") or row.get("story_title") or ""
        body = row.get("comment_text") or row.get("story_text") or ""
        text = f"{title}\n{body}".strip()[:2000]
        url = row.get("url") or f"https://news.ycombinator.com/item?id={oid}"
        hits.append(
            {
                "id": rid,
                "provider": "hackernews",
                "url": url,
                "title": title or text[:80],
                "points": row.get("points"),
                "created_at": row.get("created_at"),
                "text": text,
                "framed": frame_retrieved_data(
                    document_id=rid,
                    chunk_id=rid,
                    authored_at=str(row.get("created_at") or ""),
                    text=text,
                ),
            }
        )
    return {
        "hits": hits,
        "provider": "hackernews",
        "mode": "public",
        "query": short,
    }

async def stackoverflow_search(*, query: str, limit: int = 5) -> dict[str, Any]:
    """Public Stack Exchange API — forum/Q&A signal without OAuth."""
    cleaned = scrub_query(query, fallback="SDK deployment failure")
    short = " ".join(cleaned.split()[:4]) or cleaned
    params: dict[str, Any] = {
        "order": "desc",
        "sort": "relevance",
        "q": short,
        "site": "stackoverflow",
        "pagesize": min(limit, 10),
        "filter": "default",
    }
    key = _secret("SYNAPSE_STACKEXCHANGE_KEY")
    if key:
        params["key"] = key
    try:
        async with httpx.AsyncClient(timeout=15.0, headers={"User-Agent": _USER_AGENT}) as client:
            r = await _resilient_get(
                client,
                "https://api.stackexchange.com/2.3/search/advanced",
                params=params,
            )
            if r.status_code >= 400:
                return {
                    "error": f"so_http_{r.status_code}",
                    "detail": r.text[:300],
                    "hits": [],
                    "provider": "stackoverflow",
                    "mode": "public",
                }
            rows = r.json().get("items") or []
            if not rows:
                params["q"] = "sdk deployment"
                r = await _resilient_get(
                    client,
                    "https://api.stackexchange.com/2.3/search/advanced",
                    params=params,
                )
                rows = (r.json().get("items") or []) if r.status_code < 400 else []
    except (httpx.HTTPError, HttpStatusError) as exc:
        return {
            "error": f"so_network_{type(exc).__name__}",
            "hits": [],
            "provider": "stackoverflow",
            "mode": "public",
        }

    hits = []
    for row in rows[:limit]:
        qid = row.get("question_id")
        rid = f"so_{qid}"
        title = row.get("title") or ""
        tags = ", ".join(row.get("tags") or [])
        text = f"{title}\nTags: {tags}\nScore: {row.get('score')}"[:2000]
        hits.append(
            {
                "id": rid,
                "provider": "stackoverflow",
                "url": row.get("link"),
                "title": title,
                "score": row.get("score"),
                "is_answered": row.get("is_answered"),
                "creation_date": row.get("creation_date"),
                "text": text,
                "framed": frame_retrieved_data(
                    document_id=rid,
                    chunk_id=rid,
                    authored_at=str(row.get("creation_date") or ""),
                    text=text,
                ),
            }
        )
    return {
        "hits": hits,
        "provider": "stackoverflow",
        "mode": "public",
        "query": short,
    }


async def _resilient_post(
    client: httpx.AsyncClient,
    url: str,
    *,
    json_body: dict[str, Any],
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    """Idempotent POST with backoff on 429/5xx and transport blips."""

    async def _once() -> httpx.Response:
        r = await client.post(url, json=json_body, headers=headers)
        if is_retryable_http_status(r.status_code):
            raise HttpStatusError(r.status_code, r.text)
        return r

    return await with_retry(_once, policy=_HTTP_RETRY)


async def tavily_search(*, query: str, limit: int = 5) -> dict[str, Any]:
    """Tavily web search. A missing key is a gap, not a crash."""
    key = _secret("SYNAPSE_TAVILY_API_KEY", "TAVILY_API_KEY")
    if not key:
        return {
            "error": "tavily_unavailable",
            "unavailable": True,
            "hits": [],
            "provider": "tavily",
            "mode": "missing_key",
        }
    cleaned = scrub_query(query, fallback="vendor SDK deployment failure")
    short = " ".join(cleaned.split()[:8]) or cleaned
    n = max(1, min(int(limit), 5))
    try:
        async with httpx.AsyncClient(timeout=20.0, headers={"User-Agent": _USER_AGENT}) as client:
            r = await _resilient_post(
                client,
                "https://api.tavily.com/search",
                json_body={
                    "query": short,
                    "max_results": n,
                    "search_depth": "basic",
                    "include_answer": False,
                },
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
            )
            if r.status_code != 200:
                return {
                    "error": f"tavily_http_{r.status_code}",
                    "hits": [],
                    "provider": "tavily",
                    "mode": "authenticated",
                }
            payload = r.json()
    except (httpx.HTTPError, HttpStatusError) as exc:
        return {
            "error": f"tavily_network_{type(exc).__name__}",
            "hits": [],
            "provider": "tavily",
            "mode": "authenticated",
        }
    hits: list[dict[str, Any]] = []
    for row in (payload.get("results") or [])[:n]:
        url = str(row.get("url") or "")
        title = str(row.get("title") or "")
        content = str(row.get("content") or "")
        body = f"{title}\n{content}".strip()
        rid = "tvy_" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
        hits.append(
            {
                "id": rid,
                "provider": "tavily",
                "url": url,
                "title": title,
                "score": row.get("score"),
                "text": body,
                "framed": frame_retrieved_data(
                    document_id=rid,
                    chunk_id=rid,
                    authored_at="",
                    text=body,
                ),
            }
        )
    return {
        "hits": hits,
        "provider": "tavily",
        "mode": "authenticated",
        "query": short,
    }
