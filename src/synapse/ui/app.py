"""Synapse demo UI — chat + Demo Lab over the real API (no fake answers)."""

from __future__ import annotations

import os
from typing import Any

import httpx
import streamlit as st

API = os.environ.get("SYNAPSE_API_URL", "http://127.0.0.1:8000").rstrip("/")
DEMO_USERS = [
    "uma.berg.0@northwind.example",
    "rosa.nguyen.4@northwind.example",
    "quinn.novak.0@globex.example",
]
PROJECTS = ["ATLAS", "HARBOR", "QUAY", "BEACON", "LUMEN"]
STARTERS = [
    "Summarize what changed in ATLAS during Q2 and identify the major risks.",
    "What is blocking ATLAS right now?",
    "Which open risks on ATLAS look critical?",
]

st.set_page_config(
    page_title="Synapse",
    page_icon="⬡",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
<style>
  .stApp { background: linear-gradient(180deg, #f7f6f3 0%, #eef1f4 100%); }
  [data-testid="stSidebar"] { background: #1c2430; }
  [data-testid="stSidebar"] * { color: #e8ecf1 !important; }
  [data-testid="stSidebar"] .stTextInput input,
  [data-testid="stSidebar"] .stSelectbox div[data-baseweb="select"] > div {
    background: #2a3444 !important; color: #e8ecf1 !important; border-color: #3d4a5c !important;
  }
  [data-testid="stSidebar"] button { border-color: #5b6b7c !important; }
  h1 { font-weight: 650 !important; letter-spacing: -0.02em; color: #1c2430 !important; }
  .synapse-sub { color: #5a6573; margin-top: -0.6rem; margin-bottom: 1.2rem; }
  .meta-chip {
    display: inline-block; font-size: 0.78rem; padding: 0.15rem 0.55rem;
    border-radius: 999px; background: #e4e9ef; color: #334155; margin-right: 0.35rem;
  }
</style>
""",
    unsafe_allow_html=True,
)


def _init_state() -> None:
    defaults: dict[str, Any] = {
        "token": None,
        "identity": None,
        "messages": [],
        "project_key": "ATLAS",
        "live_status": None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {st.session_state.token}",
        "Content-Type": "application/json",
    }


def _login(email: str, password: str) -> None:
    r = httpx.post(
        f"{API}/v1/auth/token",
        json={"email": email, "password": password},
        timeout=30.0,
    )
    r.raise_for_status()
    data = r.json()
    st.session_state.token = data["access_token"]
    st.session_state.identity = data
    st.session_state.messages = []
    st.session_state.live_status = None


def _ask(question: str, project_key: str) -> dict[str, Any]:
    r = httpx.post(
        f"{API}/v1/ask",
        headers=_headers(),
        json={"question": question, "project_key": project_key},
        timeout=180.0,
    )
    if r.status_code >= 400:
        detail = r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text
        raise httpx.HTTPStatusError(
            f"ask failed {r.status_code}: {detail}",
            request=r.request,
            response=r,
        )
    return r.json()


def _multihop(question: str, project_key: str) -> dict[str, Any]:
    r = httpx.post(
        f"{API}/v1/multihop",
        headers=_headers(),
        json={"question": question, "project_key": project_key},
        timeout=120.0,
    )
    r.raise_for_status()
    return r.json()


def _get_project(project_key: str) -> dict[str, Any] | None:
    r = httpx.get(f"{API}/v1/projects/{project_key}", headers=_headers(), timeout=20.0)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def _patch_status(project_key: str, status: str) -> dict[str, Any]:
    r = httpx.patch(
        f"{API}/v1/projects/{project_key}/status",
        headers=_headers(),
        json={"status": status},
        timeout=20.0,
    )
    r.raise_for_status()
    return r.json()


def _write_memory(content: str, project_key: str) -> dict[str, Any]:
    r = httpx.post(
        f"{API}/v1/memory",
        headers=_headers(),
        json={"content": content, "project_key": project_key, "kind": "fact"},
        timeout=20.0,
    )
    r.raise_for_status()
    return r.json()


def _search_memory(q: str, project_key: str) -> dict[str, Any]:
    r = httpx.get(
        f"{API}/v1/memory/search",
        headers=_headers(),
        params={"q": q, "project_key": project_key, "limit": 5},
        timeout=20.0,
    )
    r.raise_for_status()
    return r.json()


def _search_docs(project_key: str, q: str) -> list[dict[str, Any]]:
    r = httpx.get(
        f"{API}/v1/projects/{project_key}/search",
        headers=_headers(),
        params={"q": q, "limit": 5},
        timeout=60.0,
    )
    r.raise_for_status()
    return r.json()


def _metrics() -> dict[str, Any] | None:
    try:
        r = httpx.get(f"{API}/v1/metrics", headers=_headers(), timeout=15.0)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


def _connectors() -> dict[str, Any] | None:
    try:
        r = httpx.get(f"{API}/v1/connectors", headers=_headers(), timeout=15.0)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


def _render_assistant(payload: dict[str, Any]) -> None:
    answer = payload.get("answer") or "*(No answer returned.)*"
    st.markdown(answer)

    run_id = payload.get("run_id")
    usage = payload.get("usage") or {}
    chips = []
    if run_id:
        chips.append(f'<span class="meta-chip">run {run_id[:12]}…</span>')
    if usage.get("tool_calls") is not None:
        chips.append(f'<span class="meta-chip">{usage["tool_calls"]} tool calls</span>')
    if usage.get("total_tokens"):
        chips.append(f'<span class="meta-chip">{usage["total_tokens"]} tokens</span>')
    cost = usage.get("cost_usd") or {}
    if isinstance(cost, dict) and cost.get("total_usd") is not None:
        chips.append(f'<span class="meta-chip">~${cost["total_usd"]:.4f}</span>')
    if payload.get("memory_id"):
        chips.append(f'<span class="meta-chip">ltm {str(payload["memory_id"])[:10]}…</span>')
    if payload.get("conflicts"):
        chips.append(f'<span class="meta-chip">{len(payload["conflicts"])} conflicts</span>')
    if chips:
        st.markdown(" ".join(chips), unsafe_allow_html=True)

    citations = payload.get("citations") or []
    gaps = payload.get("gaps") or []
    hops = payload.get("hops") or []
    with st.expander("Evidence — citations, gaps, hops", expanded=bool(gaps or citations)):
        c1, c2 = st.columns(2)
        with c1:
            st.caption("Citations (grounded)")
            if citations:
                for c in citations:
                    rid = c.get("record_id") or c.get("id") or "?"
                    kind = c.get("kind") or c.get("source_kind") or ""
                    st.markdown(f"- `{rid}` {kind}")
            else:
                st.write("None")
            rejected = payload.get("rejected_citations") or []
            if rejected:
                st.caption("Rejected (ungrounded)")
                st.write(", ".join(str(x) for x in rejected))
        with c2:
            st.caption("Gaps")
            st.write(gaps if gaps else "None")
            st.caption("Conflicts")
            st.write(payload.get("conflicts") or "None")
        if hops:
            st.caption("Multi-hop follow")
            for h in hops:
                ok = "✓" if h.get("ok") else "·"
                st.markdown(f"{ok} **{h.get('tool')}** — {h.get('reason', '')}")

    with st.expander("Run detail — plan, checklist, trail"):
        st.json(
            {
                "plan": payload.get("plan"),
                "checklist": payload.get("checklist"),
                "tool_trail": payload.get("tool_trail"),
                "usage": usage,
                "guardrail": payload.get("guardrail"),
            }
        )


_init_state()

with st.sidebar:
    st.markdown("### Synapse")
    st.caption("Delivery Q&A over live data")
    st.divider()
    st.markdown("**Sign in**")
    email = st.selectbox("User", DEMO_USERS, index=0)
    password = st.text_input("Password", type="password", value="synapse-demo")
    if st.button("Sign in", use_container_width=True):
        try:
            _login(email, password)
            st.rerun()
        except Exception as exc:
            st.session_state.token = None
            st.session_state.identity = None
            st.error(str(exc))

    if st.session_state.identity:
        ident = st.session_state.identity
        st.caption(f"{ident.get('role')} · {ident.get('tenant_id')}")
        if st.button("Sign out", use_container_width=True):
            st.session_state.token = None
            st.session_state.identity = None
            st.session_state.messages = []
            st.rerun()

        st.divider()
        st.session_state.project_key = st.selectbox(
            "Project",
            PROJECTS,
            index=PROJECTS.index(st.session_state.project_key)
            if st.session_state.project_key in PROJECTS
            else 0,
        )
        if st.button("Refresh live status", use_container_width=True):
            try:
                proj = _get_project(st.session_state.project_key)
                st.session_state.live_status = proj.get("status") if proj else "not found"
            except Exception as exc:
                st.session_state.live_status = f"error: {exc}"
        if st.session_state.live_status:
            st.caption(f"Live status: `{st.session_state.live_status}`")

        if st.button("Clear chat", use_container_width=True):
            st.session_state.messages = []
            st.rerun()

        st.divider()
        st.markdown("**Demo Lab**")
        st.caption("Mutates real data / probes real guards")

        new_status = st.selectbox(
            "Set project status",
            ["at_risk", "active", "delayed", "on_hold", "completed"],
            index=0,
        )
        if st.button("Apply live status", use_container_width=True):
            try:
                updated = _patch_status(st.session_state.project_key, new_status)
                st.session_state.live_status = updated["status"]
                st.success(f"Live `{updated['key']}` → `{updated['status']}`")
            except Exception as exc:
                st.error(str(exc))

        mem_note = st.text_input(
            "LTM note",
            value="Remember: stakeholder asked to track Harbor SDK slip for ATLAS weekly.",
        )
        if st.button("Write LTM note", use_container_width=True):
            try:
                mid = _write_memory(mem_note, st.session_state.project_key)
                st.success(f"Wrote `{mid['id']}`")
            except Exception as exc:
                st.error(str(exc))
        if st.button("Search LTM", use_container_width=True):
            try:
                hits = _search_memory("Harbor SDK", st.session_state.project_key)
                st.json(hits)
            except Exception as exc:
                st.error(str(exc))

        rag_q = st.text_input("RAG probe query", value="June cutover risks on track")
        if st.button("Semantic doc search", use_container_width=True):
            try:
                hits = _search_docs(st.session_state.project_key, rag_q)
                st.session_state.messages.append(
                    {"role": "assistant", "kind": "rag", "content": hits}
                )
                st.rerun()
            except Exception as exc:
                st.error(str(exc))

        if st.button("Smoke multi-hop (no LLM)", use_container_width=True):
            try:
                with st.spinner("gather → follow → pack…"):
                    mh = _multihop("Q2 risks and blockers", st.session_state.project_key)
                st.session_state.messages.append(
                    {"role": "assistant", "kind": "multihop", "content": mh}
                )
                st.rerun()
            except Exception as exc:
                st.error(str(exc))

        if st.button("Show /v1/metrics", use_container_width=True):
            snap = _metrics()
            if snap:
                st.session_state.messages.append(
                    {"role": "assistant", "kind": "metrics", "content": snap}
                )
                st.rerun()
            else:
                st.error("metrics unavailable")

        st.divider()
        st.caption("Live connectors")
        catalog = _connectors()
        if catalog:
            providers = catalog.get("providers") or {}
            live = providers.get("live_db") or {}
            pub = providers.get("public_external") or {}
            st.write("DB:", "ready" if live.get("ready") else "—")
            for name, meta in pub.items():
                mode = (meta or {}).get("mode", "?")
                st.write(f"{name}: {mode}")
        else:
            st.write("Unavailable")

st.markdown("# Synapse")
st.markdown(
    '<p class="synapse-sub">Chat with the delivery agent — each message runs a bounded '
    "ask over live Postgres, docs, and public externals. Demo Lab mutates real live state.</p>",
    unsafe_allow_html=True,
)

if not st.session_state.token:
    st.info(
        "Sign in from the sidebar. Demo password: `synapse-demo`. "
        "Northwind admin · Northwind viewer · Globex admin (tenant isolation)."
    )
    st.stop()

for msg in st.session_state.messages:
    role = msg["role"]
    with st.chat_message(role):
        if role == "user":
            st.markdown(msg["content"])
            if msg.get("project_key"):
                st.caption(f"Project {msg['project_key']}")
        elif msg.get("kind") == "multihop":
            body = msg["content"]
            st.markdown("**Multi-hop smoke (no LLM)**")
            st.write(
                f"Status `{body.get('live_status')}` · "
                f"{body.get('item_count', 0)} evidence items"
            )
            st.json(
                {
                    "sources_present": body.get("sources_present"),
                    "external_providers": body.get("external_providers"),
                    "hops": body.get("hops"),
                    "gaps": body.get("gaps"),
                    "conflicts": body.get("conflicts"),
                }
            )
        elif msg.get("kind") == "rag":
            st.markdown("**Semantic document search**")
            st.json(msg["content"])
        elif msg.get("kind") == "metrics":
            st.markdown("**Process metrics**")
            st.caption(msg["content"].get("note", ""))
            st.json(
                {
                    "counters": msg["content"].get("counters"),
                    "timings": msg["content"].get("timings"),
                }
            )
        elif msg.get("kind") == "error":
            st.error(msg["content"])
        else:
            _render_assistant(msg["content"])

if not st.session_state.messages:
    st.caption("Try a starter")
    cols = st.columns(len(STARTERS))
    for i, text in enumerate(STARTERS):
        if cols[i].button(text[:42] + ("…" if len(text) > 42 else ""), key=f"starter_{i}"):
            st.session_state._pending = text
            st.rerun()

pending = st.session_state.pop("_pending", None)
prompt = st.chat_input(f"Ask about {st.session_state.project_key}…")
if pending:
    prompt = pending

if prompt:
    project = st.session_state.project_key
    st.session_state.messages.append(
        {"role": "user", "content": prompt, "project_key": project}
    )
    with st.chat_message("user"):
        st.markdown(prompt)
        st.caption(f"Project {project}")

    with st.chat_message("assistant"):
        with st.spinner("Agent running (gather → follow → probe → answer)…"):
            try:
                data = _ask(prompt, project)
                st.session_state.messages.append(
                    {"role": "assistant", "kind": "ask", "content": data}
                )
                _render_assistant(data)
            except httpx.HTTPStatusError as exc:
                err = str(exc)
                # Prefer JSON detail for guardrail demos
                try:
                    body = exc.response.json()
                    detail = body.get("detail", body)
                    err = f"HTTP {exc.response.status_code}: {detail}"
                except Exception:
                    pass
                st.session_state.messages.append(
                    {"role": "assistant", "kind": "error", "content": err}
                )
                st.error(err)
            except Exception as exc:
                err = str(exc)
                st.session_state.messages.append(
                    {"role": "assistant", "kind": "error", "content": err}
                )
                st.error(err)
