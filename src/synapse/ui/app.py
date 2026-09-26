"""Synapse demo UI — chat + Demo Lab over the real API (no fake answers)."""

from __future__ import annotations

import json
import os
from typing import Any
from uuid import uuid4

import httpx
import streamlit as st

from synapse.data.catalog import ATLAS_CUTOVER_ASK

API = os.environ.get("SYNAPSE_API_URL", "http://127.0.0.1:8000").rstrip("/")
DEMO_USERS = [
    "uma.berg.0@northwind.example",
    "rosa.nguyen.4@northwind.example",
    "quinn.novak.0@globex.example",
]
PROJECTS = ["ATLAS", "HARBOR"]
STARTERS = [
    ATLAS_CUTOVER_ASK,
    "What is blocking the Atlas vendor SDK right now?",
    "What is Harbor Identity's status, and how does that block the Atlas freeze?",
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


def _ask(question: str, project_key: str, *, idempotency_key: str) -> dict[str, Any]:
    headers = _headers()
    headers["Idempotency-Key"] = idempotency_key
    r = httpx.post(
        f"{API}/v1/ask",
        headers=headers,
        json={"question": question, "project_key": project_key},
        timeout=180.0,
    )
    if r.status_code >= 400:
        detail = (
            r.json()
            if r.headers.get("content-type", "").startswith("application/json")
            else r.text
        )
        raise httpx.HTTPStatusError(
            f"ask failed {r.status_code}: {detail}",
            request=r.request,
            response=r,
        )
    return r.json()


def _format_trace(events: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for event in events:
        label = str(event.get("label") or event.get("step") or "step")
        raw_detail = event.get("detail")
        detail: dict[str, Any] = raw_detail if isinstance(raw_detail, dict) else {}
        bits: list[str] = []
        slots = detail.get("slots") or []
        if slots:
            bits.append(", ".join(str(slot) for slot in slots))
        tools = detail.get("tools") or detail.get("hops") or []
        names: list[str] = []
        for row in tools:
            if isinstance(row, dict) and row.get("tool"):
                mark = "ok" if row.get("ok", True) else "failed"
                names.append(f"{row['tool']} ({mark})")
            elif isinstance(row, str):
                names.append(row)
        if names:
            bits.append(", ".join(names))
        if detail.get("item_count") is not None:
            bits.append(
                f"{detail.get('item_count')} items, "
                f"{detail.get('conflict_count', 0)} conflicts, "
                f"{detail.get('gap_count', 0)} gaps"
            )
        if detail.get("citation_count") is not None:
            bits.append(
                f"{detail.get('citation_count')} citations, "
                f"{detail.get('rejected_count', 0)} rejected"
            )
        if detail.get("probe_count") is not None:
            bits.append(f"probe {detail.get('probe_count')}")
        extra = f" — {'; '.join(bits)}" if bits else ""
        lines.append(f"- **{label}**{extra}")
    return "\n".join(lines) if lines else "_Waiting for the first step._"


def _ask_stream(
    question: str,
    project_key: str,
    *,
    idempotency_key: str,
    box: Any,
) -> dict[str, Any]:
    """Read POST /v1/ask/stream. Events are the real graph nodes, then the final JSON."""
    headers = _headers()
    headers["Idempotency-Key"] = idempotency_key
    events: list[dict[str, Any]] = []
    result: dict[str, Any] | None = None
    with httpx.stream(
        "POST",
        f"{API}/v1/ask/stream",
        headers=headers,
        json={"question": question, "project_key": project_key},
        timeout=180.0,
    ) as response:
        if response.status_code >= 400:
            raw = response.read()
            try:
                detail = json.loads(raw)
            except json.JSONDecodeError:
                detail = raw.decode(errors="replace")
            raise httpx.HTTPStatusError(
                f"ask failed {response.status_code}: {detail}",
                request=response.request,
                response=response,
            )
        for line in response.iter_lines():
            if not line or not line.startswith("data:"):
                continue
            message = json.loads(line[5:].strip())
            kind = message.get("type")
            if kind == "event":
                events.append(message)
                box.markdown(_format_trace(events))
            elif kind == "error":
                raise RuntimeError(str(message.get("detail") or message))
            elif kind == "done":
                body = message.get("result")
                if isinstance(body, dict):
                    result = body
    if result is None:
        raise RuntimeError("ask stream ended without a result")
    result["events"] = events
    return result


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


def _tool_lane(tool: str) -> str:
    t = (tool or "").lower()
    if t in {
        "project_lookup",
        "risk_list",
        "blocker_list",
        "project_activity",
        "task_search",
        "email_search",
        "meeting_search",
    }:
        return "LIVE"
    if t == "document_search":
        return "RAG"
    if t in {"memory_search", "memory_write"}:
        return "LTM"
    if t in {
        "hn_search",
        "stackoverflow_search",
        "tavily_search",
    }:
        return "EXTERNAL"
    return "OTHER"


def _render_hops(hops: list[dict[str, Any]], trail: list[Any] | None = None) -> None:
    """Structured hop trail — execution evidence, not hidden chain-of-thought."""
    st.markdown("#### Tools / hops")
    if hops:
        for i, h in enumerate(hops, start=1):
            tool = h.get("tool") or "step"
            lane = _tool_lane(str(tool))
            reason = (h.get("reason") or "").strip() or "from prior evidence"
            srcs = h.get("source_ids") or []
            extra = f" ← `{', '.join(str(s) for s in srcs[:4])}`" if srcs else ""
            args = h.get("args") or {}
            arg_bits = []
            if isinstance(args, dict):
                for k in ("project_key", "query", "limit", "risk_id"):
                    if k in args and args[k] is not None:
                        v = str(args[k])
                        if len(v) > 48:
                            v = v[:45] + "…"
                        arg_bits.append(f"{k}={v}")
            arg_s = f" · ({', '.join(arg_bits)})" if arg_bits else ""
            mark = "✓" if h.get("ok", True) else "×"
            st.markdown(f"{i}. {mark} **[{lane}] {tool}** — {reason}{extra}{arg_s}")
        return
    if trail:
        for i, step in enumerate(trail, start=1):
            if isinstance(step, dict):
                name = step.get("tool") or step.get("name") or step.get("node") or "step"
                st.markdown(f"{i}. **[{_tool_lane(str(name))}] {name}**")
            else:
                st.markdown(f"{i}. {step}")
        return
    st.caption("No hops recorded (run stopped early).")


def _render_assistant(payload: dict[str, Any], question: str | None = None) -> None:
    """Product story: plan → tools → evidence lanes → guards → answer."""
    st.markdown("### Execution")
    events = payload.get("events") or []
    if events:
        st.markdown("#### Trace")
        st.markdown(_format_trace(events))
        st.caption("Steps emitted while this ask ran. Tool names and counts only.")

    if question:
        st.markdown("#### Question")
        st.write(question)

    plan = payload.get("plan") or []
    if plan:
        st.markdown("#### Plan (evidence slots)")
        st.write(", ".join(f"`{p}`" for p in plan))
        st.caption("Slots the planner opened — not private model chain-of-thought.")

    checklist = payload.get("checklist") or {}
    if checklist:
        filled = [k for k, v in checklist.items() if v == "filled"]
        partial = [k for k, v in checklist.items() if v == "partial"]
        empty = [k for k, v in checklist.items() if v == "empty"]
        st.markdown("#### Checklist")
        bits = []
        if filled:
            bits.append(f"filled: {', '.join(filled)}")
        if partial:
            bits.append(f"partial: {', '.join(partial)}")
        if empty:
            bits.append(f"empty: {', '.join(empty)}")
        st.caption(" · ".join(bits) if bits else "—")

    hops = payload.get("hops") or []
    trail = payload.get("tool_trail") or []
    _render_hops(hops, trail if isinstance(trail, list) else None)

    evidence = payload.get("evidence") or {}
    if evidence:
        st.markdown("#### Evidence summary")
        live = evidence.get("live_status")
        by_kind = evidence.get("by_kind") or {}
        st.write(
            f"Live status: `{live}` · items: **{evidence.get('item_count', 0)}** · "
            f"by kind: {by_kind or '{}'}"
        )
        if evidence.get("gap_count") or evidence.get("conflict_count"):
            st.caption(
                f"gaps: {evidence.get('gap_count', 0)} · "
                f"conflicts: {evidence.get('conflict_count', 0)} "
                "(LIVE ≻ EXTERNAL ≻ RAG ≻ LTM)"
            )
        else:
            st.caption("Trust order: LIVE ≻ EXTERNAL ≻ RAG ≻ LTM")

    guard = payload.get("guardrail") or {}
    rejected = payload.get("rejected_citations") or []
    st.markdown("#### Guardrails / citation check")
    st.caption(
        f"input: {guard.get('input', 'allowed')} · "
        f"citation_check: {guard.get('citation_check', 'grounded')} · "
        f"rejected: {len(rejected)}"
        + (f" ({', '.join(str(x) for x in rejected[:6])})" if rejected else "")
    )
    if guard.get("flags"):
        st.caption(f"soft flags: {', '.join(str(f) for f in guard['flags'])}")

    st.markdown("#### Final answer")
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
    if hops:
        chips.append(f'<span class="meta-chip">{len(hops)} hops</span>')
    if chips:
        st.markdown(" ".join(chips), unsafe_allow_html=True)

    citations = payload.get("citations") or []
    gaps = payload.get("gaps") or []
    with st.expander("Evidence — citations, gaps, conflicts", expanded=bool(citations or gaps)):
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
            if rejected:
                st.caption("Rejected (ungrounded)")
                st.write(", ".join(str(x) for x in rejected))
        with c2:
            st.caption("Gaps")
            st.write(gaps if gaps else "None")
            st.caption("Conflicts")
            st.write(payload.get("conflicts") or "None")
            st.caption("Memory")
            st.write(
                "STM = this run’s checkpoints in Postgres. "
                "LTM note id: "
                + (str(payload.get("memory_id")) if payload.get("memory_id") else "none written")
            )

    with st.expander("Run detail — plan, checklist, trail"):
        st.json(
            {
                "plan": payload.get("plan"),
                "checklist": payload.get("checklist"),
                "hops": hops,
                "tool_trail": trail,
                "evidence": evidence,
                "usage": usage,
                "guardrail": payload.get("guardrail"),
            }
        )


_init_state()

with st.sidebar:
    st.markdown("### Synapse")
    st.caption("Atlas cutover go/no-go")
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
        st.caption("Project 4 = live / RAG / memory · Project 5 = guards")

        st.markdown("*Project 4 — live data*")
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

        st.markdown("*Project 4 — memory*")
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

        st.markdown("*Project 4 — RAG + multi-hop*")
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

        st.markdown("*Project 5 — production checks*")
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
    '<p class="synapse-sub">Northwind Logistics needs a go/no-go on the '
    "<b>Atlas platform cutover</b> before the 30 June freeze. "
    "Harbor Identity's vendor SDK missed its drop. "
    "Each message hits real <code>/v1/ask</code> and combines live status, "
    "the status report, mail, meetings, notes, and public web.</p>",
    unsafe_allow_html=True,
)

if not st.session_state.token:
    st.info(
        "Sign in from the sidebar. Demo password: `synapse-demo`. "
        "Northwind admin · Northwind viewer · Globex admin (tenant isolation)."
    )
    st.stop()

for idx, msg in enumerate(st.session_state.messages):
    role = msg["role"]
    with st.chat_message(role):
        if role == "user":
            st.markdown(msg["content"])
            if msg.get("project_key"):
                st.caption(f"Project {msg['project_key']}")
        elif msg.get("kind") == "multihop":
            body = msg["content"]
            st.markdown("**Multi-hop smoke (no LLM)** — live gather + follow hops")
            st.write(
                f"Status `{body.get('live_status')}` · "
                f"{body.get('item_count', 0)} evidence items"
            )
            _render_hops(body.get("hops") or [])
            st.json(
                {
                    "sources_present": body.get("sources_present"),
                    "external_providers": body.get("external_providers"),
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
            q = None
            if idx > 0 and st.session_state.messages[idx - 1].get("role") == "user":
                q = st.session_state.messages[idx - 1].get("content")
            _render_assistant(msg["content"], question=q)

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
        trace = st.empty()
        trace.markdown("_Request accepted — waiting for the first graph step._")
        try:
            data = _ask_stream(
                prompt,
                project,
                idempotency_key=uuid4().hex,
                box=trace,
            )
            st.session_state.messages.append(
                {"role": "assistant", "kind": "ask", "content": data}
            )
            _render_assistant(data, question=prompt)
        except httpx.HTTPStatusError as exc:
            err = str(exc)
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
