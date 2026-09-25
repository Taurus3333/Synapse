"""Live external connectors — heterogeneous sources beside the synthetic DB.

Default demo path needs **no personal credentials**:
  - github_search  → public GitHub Issues API (default repo: kubernetes/kubernetes)
  - slack_search   → Slack if token set, else Hacker News Algolia (public)
  - gmail_search   → Gmail if token set, else Stack Overflow (public)

Optional tokens raise rate limits / unlock private workspaces. Missing private
tokens never crash the agent — public fallbacks keep EXTERNAL evidence live.
All retrieved text is framed as DATA, not instructions.
"""

from __future__ import annotations

import base64
import os
import re
from typing import Any

import httpx

from synapse.evidence.frame import frame_retrieved_data
from synapse.reliability.http import HttpStatusError
from synapse.reliability.retry import RetryPolicy, is_retryable_http_status, with_retry

# Synthetic project keys — strip from public web queries so "ATLAS SDK" still
# finds real kubernetes/HN/SO hits about SDK / delivery risk language.
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


def public_github_repo() -> str:
    raw = (
        os.environ.get("SYNAPSE_GITHUB_PUBLIC_REPO", "").strip()
        or "kubernetes/kubernetes"
    )
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", raw):
        return "kubernetes/kubernetes"
    return raw


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


def github_issue_query(query: str) -> str:
    """Build a GitHub search clause that ORs key tokens (AND of many words → empty)."""
    cleaned = scrub_query(query)
    tokens = [t for t in cleaned.replace(" OR ", " ").split() if t.upper() != "OR"]
    if not tokens:
        tokens = ["risk", "blocker", "SDK"]
    # Cap tokens; OR keeps recall on a busy public repo
    core = " OR ".join(tokens[:4])
    return f"repo:{public_github_repo()} is:issue is:open ({core})"


def connector_status() -> dict[str, Any]:
    """Full connector catalog: live DB + public externals + optional private."""
    gh_token = bool(_secret("SYNAPSE_GITHUB_TOKEN", "GITHUB_TOKEN"))
    slack_token = bool(_secret("SYNAPSE_SLACK_BOT_TOKEN", "SLACK_BOT_TOKEN"))
    gmail_token = bool(_secret("SYNAPSE_GMAIL_ACCESS_TOKEN", "GMAIL_ACCESS_TOKEN"))
    repo = public_github_repo()
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
            "github": {
                "ready": True,
                "mode": "authenticated" if gh_token else "public",
                "provider": "github",
                "target": repo,
                "authenticated": gh_token,
                "tool": "github_search",
            },
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
            "wikipedia": {
                "ready": True,
                "mode": "public",
                "provider": "wikipedia",
                "tool": "wikipedia_search",
            },
        },
        "optional_private": {
            "slack": {
                "ready": True,
                "mode": "authenticated" if slack_token else "public_fallback",
                "provider": "slack" if slack_token else "hackernews",
                "authenticated": slack_token,
                "tool": "slack_search",
            },
            "gmail": {
                "ready": True,
                "mode": "authenticated" if gmail_token else "public_fallback",
                "provider": "gmail" if gmail_token else "stackoverflow",
                "authenticated": gmail_token,
                "tool": "gmail_search",
            },
        },
        # Flat aliases for older clients / simple demos
        "github": {
            "ready": True,
            "mode": "authenticated" if gh_token else "public",
            "provider": "github",
            "target": repo,
            "authenticated": gh_token,
        },
        "slack": {
            "ready": True,
            "mode": "authenticated" if slack_token else "public_fallback",
            "provider": "slack" if slack_token else "hackernews",
            "authenticated": slack_token,
        },
        "gmail": {
            "ready": True,
            "mode": "authenticated" if gmail_token else "public_fallback",
            "provider": "gmail" if gmail_token else "stackoverflow",
            "authenticated": gmail_token,
        },
        "hackernews": {"ready": True, "mode": "public", "provider": "hackernews"},
        "stackoverflow": {"ready": True, "mode": "public", "provider": "stackoverflow"},
        "wikipedia": {"ready": True, "mode": "public", "provider": "wikipedia"},
    }


async def probe_connectors() -> dict[str, Any]:
    """Live liveness checks against public endpoints (+ live_db always ok)."""
    out: dict[str, Any] = {
        "live_db": {"ok": True, "provider": "postgres", "mode": "sql"},
    }
    async with httpx.AsyncClient(timeout=12.0, headers={"User-Agent": _USER_AGENT}) as client:
        out["github"] = await _probe_github(client)
        out["hackernews"] = await _probe_hn(client)
        out["stackoverflow"] = await _probe_so(client)
        out["wikipedia"] = await _probe_wikipedia(client)
    status = connector_status()
    ready = all(v.get("ok") for v in out.values())
    return {"ready": ready, "probes": out, "providers": status}


async def _probe_wikipedia(client: httpx.AsyncClient) -> dict[str, Any]:
    try:
        r = await client.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "opensearch",
                "search": "Kubernetes",
                "limit": 1,
                "namespace": 0,
                "format": "json",
            },
        )
        return {"ok": r.status_code == 200, "status_code": r.status_code, "provider": "wikipedia"}
    except httpx.HTTPError as exc:
        return {"ok": False, "error": type(exc).__name__, "provider": "wikipedia"}


async def _probe_github(client: httpx.AsyncClient) -> dict[str, Any]:
    repo = public_github_repo()
    headers = _github_headers()
    try:
        r = await client.get(f"https://api.github.com/repos/{repo}", headers=headers)
        return {
            "ok": r.status_code == 200,
            "status_code": r.status_code,
            "target": repo,
            "mode": "authenticated" if _secret("SYNAPSE_GITHUB_TOKEN", "GITHUB_TOKEN") else "public",
        }
    except httpx.HTTPError as exc:
        return {"ok": False, "error": type(exc).__name__, "target": repo}


async def _probe_hn(client: httpx.AsyncClient) -> dict[str, Any]:
    try:
        r = await client.get(
            "https://hn.algolia.com/api/v1/search",
            params={"query": "kubernetes", "hitsPerPage": 1, "tags": "story"},
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
                "q": "kubernetes",
                "site": "stackoverflow",
                "pagesize": 1,
            },
        )
        return {"ok": r.status_code == 200, "status_code": r.status_code, "provider": "stackoverflow"}
    except httpx.HTTPError as exc:
        return {"ok": False, "error": type(exc).__name__, "provider": "stackoverflow"}


def _github_headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": _USER_AGENT,
    }
    token = _secret("SYNAPSE_GITHUB_TOKEN", "GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


async def github_search(*, query: str, limit: int = 5) -> dict[str, Any]:
    """Search issues/PRs on a public GitHub repo (default kubernetes/kubernetes).

    No token required (60 req/hr unauthenticated). Token optional for higher quota.
    """
    repo = public_github_repo()
    q = github_issue_query(query)
    headers = _github_headers()
    mode = "authenticated" if "Authorization" in headers else "public"
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await _resilient_get(
                client,
                "https://api.github.com/search/issues",
                params={"q": q, "per_page": min(limit, 10), "sort": "updated"},
                headers=headers,
            )
            if r.status_code == 403 and mode == "public":
                # Rate-limited: fall back to recent issues list (still live).
                return await _github_recent_issues(client, repo=repo, limit=limit, mode=mode)
            if r.status_code >= 400:
                return {
                    "error": f"github_http_{r.status_code}",
                    "detail": r.text[:300],
                    "hits": [],
                    "provider": "github",
                    "mode": mode,
                    "target": repo,
                }
            items = r.json().get("items") or []
            if not items:
                return await _github_recent_issues(client, repo=repo, limit=limit, mode=mode)
    except (httpx.HTTPError, HttpStatusError) as exc:
        return {
            "error": f"github_network_{type(exc).__name__}",
            "hits": [],
            "provider": "github",
            "mode": mode,
            "target": repo,
        }

    hits = [_gh_hit(it) for it in items[:limit]]
    return {
        "hits": hits,
        "provider": "github",
        "mode": mode,
        "target": repo,
        "query": q,
    }


async def _github_recent_issues(
    client: httpx.AsyncClient, *, repo: str, limit: int, mode: str
) -> dict[str, Any]:
    """Live fallback: open issues+PRs (GitHub lists both on /issues)."""
    r = await _resilient_get(
        client,
        f"https://api.github.com/repos/{repo}/issues",
        params={"state": "open", "per_page": min(max(limit, 10), 30), "sort": "updated"},
        headers=_github_headers(),
    )
    if r.status_code >= 400:
        return {
            "error": f"github_http_{r.status_code}",
            "detail": r.text[:300],
            "hits": [],
            "provider": "github",
            "mode": mode,
            "target": repo,
        }
    # Prefer pure issues; if the page is PR-heavy, still keep PRs as live signal.
    raw = list(r.json() or [])
    issues = [it for it in raw if "pull_request" not in it]
    chosen = (issues or raw)[:limit]
    return {
        "hits": [_gh_hit(it) for it in chosen],
        "provider": "github",
        "mode": mode,
        "target": repo,
        "fallback": "recent_issues",
    }


def _gh_hit(it: dict[str, Any]) -> dict[str, Any]:
    rid = f"gh_{it.get('id')}"
    text = f"{it.get('title')}\n{it.get('body') or ''}"[:2000]
    return {
        "id": rid,
        "provider": "github",
        "url": it.get("html_url"),
        "title": it.get("title"),
        "state": it.get("state"),
        "updated_at": it.get("updated_at"),
        "text": text,
        "framed": frame_retrieved_data(
            document_id=rid,
            chunk_id=rid,
            authored_at=str(it.get("updated_at") or ""),
            text=text,
        ),
    }


async def slack_search(*, query: str, limit: int = 5) -> dict[str, Any]:
    """Workspace Slack when tokenled; otherwise live Hacker News (public discourse)."""
    token = _secret("SYNAPSE_SLACK_BOT_TOKEN", "SLACK_BOT_TOKEN")
    if token:
        return await _slack_authenticated(query=query, limit=limit, token=token)
    return await hackernews_search(query=query, limit=limit)


async def _slack_authenticated(*, query: str, limit: int, token: str) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(
                "https://slack.com/api/search.messages",
                params={"query": query, "count": min(limit, 10)},
                headers={"Authorization": f"Bearer {token}"},
            )
            data = r.json()
    except httpx.HTTPError as exc:
        return {"error": f"slack_network_{type(exc).__name__}", "hits": [], "provider": "slack"}
    if not data.get("ok"):
        return {"error": data.get("error") or f"slack_http_{r.status_code}", "hits": [], "provider": "slack"}
    matches = ((data.get("messages") or {}).get("matches")) or []
    hits = []
    for m in matches[:limit]:
        rid = f"sl_{m.get('iid') or m.get('ts')}"
        text = str(m.get("text") or "")[:2000]
        hits.append(
            {
                "id": rid,
                "provider": "slack",
                "channel": (m.get("channel") or {}).get("name"),
                "permalink": m.get("permalink"),
                "ts": m.get("ts"),
                "text": text,
                "framed": frame_retrieved_data(
                    document_id=rid,
                    chunk_id=rid,
                    authored_at=str(m.get("ts") or ""),
                    text=text,
                ),
            }
        )
    return {"hits": hits, "provider": "slack", "mode": "authenticated"}


async def hackernews_search(*, query: str, limit: int = 5) -> dict[str, Any]:
    """Public HN Algolia — live community signal, no API key."""
    cleaned = scrub_query(query, fallback="kubernetes production outage")
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
                    params={"query": "kubernetes", "hitsPerPage": min(limit, 10), "tags": "story"},
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


async def gmail_search(*, query: str, limit: int = 5) -> dict[str, Any]:
    """Gmail when OAuth token set; otherwise live Stack Overflow Q&A (public)."""
    token = _secret("SYNAPSE_GMAIL_ACCESS_TOKEN", "GMAIL_ACCESS_TOKEN")
    if token:
        return await _gmail_authenticated(query=query, limit=limit, token=token)
    return await stackoverflow_search(query=query, limit=limit)


async def _gmail_authenticated(*, query: str, limit: int, token: str) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            listed = await client.get(
                "https://gmail.googleapis.com/gmail/v1/users/me/messages",
                params={"q": query, "maxResults": min(limit, 10)},
                headers=headers,
            )
            if listed.status_code >= 400:
                return {
                    "error": f"gmail_http_{listed.status_code}",
                    "detail": listed.text[:300],
                    "hits": [],
                    "provider": "gmail",
                }
            msg_refs = listed.json().get("messages") or []
            hits = []
            for ref in msg_refs[:limit]:
                mid = ref["id"]
                detail = await client.get(
                    f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{mid}",
                    params={"format": "full"},
                    headers=headers,
                )
                if detail.status_code >= 400:
                    continue
                body = detail.json()
                headers_list = (body.get("payload") or {}).get("headers") or []
                subject = next(
                    (h["value"] for h in headers_list if h["name"].lower() == "subject"), ""
                )
                date = next(
                    (h["value"] for h in headers_list if h["name"].lower() == "date"), ""
                )
                snippet = body.get("snippet") or ""
                text = _gmail_plain(body.get("payload")) or snippet
                text = f"Subject: {subject}\n{text}"[:2000]
                rid = f"gm_{mid}"
                hits.append(
                    {
                        "id": rid,
                        "provider": "gmail",
                        "message_id": mid,
                        "subject": subject,
                        "date": date,
                        "text": text,
                        "framed": frame_retrieved_data(
                            document_id=rid,
                            chunk_id=rid,
                            authored_at=date,
                            text=text,
                        ),
                    }
                )
    except httpx.HTTPError as exc:
        return {"error": f"gmail_network_{type(exc).__name__}", "hits": [], "provider": "gmail"}
    return {"hits": hits, "provider": "gmail", "mode": "authenticated"}


async def stackoverflow_search(*, query: str, limit: int = 5) -> dict[str, Any]:
    """Public Stack Exchange API — forum/Q&A signal without OAuth."""
    cleaned = scrub_query(query, fallback="kubernetes deployment failure")
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
                params["q"] = "kubernetes sdk"
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


async def wikipedia_search(*, query: str, limit: int = 5) -> dict[str, Any]:
    """Public MediaWiki opensearch + extracts — no token."""
    cleaned = scrub_query(query, fallback="Kubernetes")
    short = " ".join(cleaned.split()[:4]) or "Kubernetes"
    try:
        async with httpx.AsyncClient(timeout=15.0, headers={"User-Agent": _USER_AGENT}) as client:
            listed = await _resilient_get(
                client,
                "https://en.wikipedia.org/w/api.php",
                params={
                    "action": "opensearch",
                    "search": short,
                    "limit": min(limit, 8),
                    "namespace": 0,
                    "format": "json",
                },
            )
            if listed.status_code >= 400:
                return {
                    "error": f"wiki_http_{listed.status_code}",
                    "hits": [],
                    "provider": "wikipedia",
                    "mode": "public",
                }
            data = listed.json()
            # opensearch: [query, [titles], [descriptions], [urls]]
            titles = list(data[1]) if isinstance(data, list) and len(data) > 1 else []
            descs = list(data[2]) if isinstance(data, list) and len(data) > 2 else []
            urls = list(data[3]) if isinstance(data, list) and len(data) > 3 else []
            if not titles:
                listed = await _resilient_get(
                    client,
                    "https://en.wikipedia.org/w/api.php",
                    params={
                        "action": "opensearch",
                        "search": "software deployment",
                        "limit": min(limit, 8),
                        "namespace": 0,
                        "format": "json",
                    },
                )
                data = listed.json() if listed.status_code < 400 else []
                titles = list(data[1]) if isinstance(data, list) and len(data) > 1 else []
                descs = list(data[2]) if isinstance(data, list) and len(data) > 2 else []
                urls = list(data[3]) if isinstance(data, list) and len(data) > 3 else []

            hits = []
            for i, title in enumerate(titles[:limit]):
                rid = f"wiki_{abs(hash(title)) % (10**10)}"
                summary = descs[i] if i < len(descs) else ""
                url = urls[i] if i < len(urls) else f"https://en.wikipedia.org/wiki/{title}"
                extract = summary
                ext = await _resilient_get(
                    client,
                    "https://en.wikipedia.org/w/api.php",
                    params={
                        "action": "query",
                        "prop": "extracts",
                        "exintro": True,
                        "explaintext": True,
                        "titles": title,
                        "format": "json",
                    },
                )
                if ext.status_code < 400:
                    pages = ((ext.json().get("query") or {}).get("pages") or {})
                    for page in pages.values():
                        extract = (page.get("extract") or summary)[:1800]
                        break
                text = f"{title}\n{extract}".strip()[:2000]
                hits.append(
                    {
                        "id": rid,
                        "provider": "wikipedia",
                        "url": url,
                        "title": title,
                        "text": text,
                        "framed": frame_retrieved_data(
                            document_id=rid,
                            chunk_id=rid,
                            authored_at="",
                            text=text,
                        ),
                    }
                )
    except (httpx.HTTPError, HttpStatusError) as exc:
        return {
            "error": f"wiki_network_{type(exc).__name__}",
            "hits": [],
            "provider": "wikipedia",
            "mode": "public",
        }
    return {
        "hits": hits,
        "provider": "wikipedia",
        "mode": "public",
        "query": short,
    }


def _gmail_plain(payload: dict[str, Any] | None) -> str:
    if not payload:
        return ""
    mime = payload.get("mimeType") or ""
    body = payload.get("body") or {}
    data = body.get("data")
    if data and mime.startswith("text/plain"):
        return base64.urlsafe_b64decode(data.encode("utf-8")).decode("utf-8", errors="replace")
    for part in payload.get("parts") or []:
        found = _gmail_plain(part)
        if found:
            return found
    if data:
        return base64.urlsafe_b64decode(data.encode("utf-8")).decode("utf-8", errors="replace")
    return ""
