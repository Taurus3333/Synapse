"""HTTP perf harness — measure /v1/ask and /v1/multihop latency + token/cost.

Talks to a live API the same way Demo Lab does. Writes docs/perf_results.json.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx

from synapse.data.catalog import ATLAS_CUTOVER_ASK
from synapse.perf.cost import estimate_run_usd, pricing_notes
from synapse.perf.usage import summarize_latencies

DEFAULT_ASK = ATLAS_CUTOVER_ASK
DEFAULT_MULTIHOP = DEFAULT_ASK
DEFAULT_EMAIL = "uma.berg.0@northwind.example"
DEFAULT_PASSWORD = "synapse-demo"


@dataclass
class Sample:
    endpoint: str
    latency_ms: float
    status: int
    usage: dict[str, Any] = field(default_factory=dict)
    run_id: str | None = None
    error: str = ""
    answer_chars: int = 0
    citation_count: int = 0
    hop_count: int = 0
    probe_steps: int = 0


@dataclass
class ScenarioReport:
    id: str
    endpoint: str
    question: str
    n: int
    latency: dict[str, Any]
    usage_totals: dict[str, Any]
    cost_usd_est: dict[str, float]
    samples: list[dict[str, Any]]
    notes: str = ""


def _token(client: httpx.Client, email: str, password: str) -> str:
    r = client.post("/v1/auth/token", json={"email": email, "password": password})
    r.raise_for_status()
    return r.json()["access_token"]


def _hdr(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _sum_usage(samples: list[Sample]) -> dict[str, Any]:
    keys = (
        "prompt_tokens",
        "completion_tokens",
        "chat_calls",
        "embed_tokens",
        "embed_calls",
        "embed_cache_hits",
        "total_tokens",
        "tool_calls",
        "probe_steps",
        "evidence_items",
    )
    totals: dict[str, Any] = {k: 0 for k in keys}
    n_ok = 0
    for s in samples:
        if s.status != 200 or s.error:
            continue
        n_ok += 1
        for k in keys:
            totals[k] += int((s.usage or {}).get(k) or 0)
    totals["n_ok"] = n_ok
    if n_ok:
        totals["avg_prompt_tokens"] = round(totals["prompt_tokens"] / n_ok, 1)
        totals["avg_completion_tokens"] = round(totals["completion_tokens"] / n_ok, 1)
        totals["avg_total_tokens"] = round(totals["total_tokens"] / n_ok, 1)
        totals["avg_tool_calls"] = round(totals["tool_calls"] / n_ok, 1)
        totals["avg_chat_calls"] = round(totals["chat_calls"] / n_ok, 1)
        totals["avg_probe_steps"] = round(totals["probe_steps"] / n_ok, 1)
    return totals


def measure_endpoint(
    client: httpx.Client,
    *,
    token: str,
    endpoint: str,
    question: str,
    project_key: str,
    repeats: int,
) -> list[Sample]:
    samples: list[Sample] = []
    for _ in range(repeats):
        started = time.perf_counter()
        try:
            r = client.post(
                endpoint,
                headers=_hdr(token),
                json={"question": question, "project_key": project_key},
            )
            elapsed = (time.perf_counter() - started) * 1000.0
            body: dict[str, Any] = {}
            try:
                body = r.json()
            except Exception:
                body = {}
            usage = dict(body.get("usage") or {})
            samples.append(
                Sample(
                    endpoint=endpoint,
                    latency_ms=round(elapsed, 2),
                    status=r.status_code,
                    usage=usage,
                    run_id=body.get("run_id"),
                    error="" if r.status_code == 200 else (body.get("detail") or r.text)[:300],
                    answer_chars=len(str(body.get("answer") or "")),
                    citation_count=len(body.get("citations") or []),
                    hop_count=len(body.get("hops") or []),
                    probe_steps=int(usage.get("probe_steps") or 0),
                )
            )
        except Exception as exc:
            elapsed = (time.perf_counter() - started) * 1000.0
            samples.append(
                Sample(
                    endpoint=endpoint,
                    latency_ms=round(elapsed, 2),
                    status=0,
                    error=str(exc)[:300],
                )
            )
    return samples


def build_report(
    *,
    scenario_id: str,
    endpoint: str,
    question: str,
    samples: list[Sample],
    notes: str = "",
) -> ScenarioReport:
    ok_lat = [s.latency_ms for s in samples if s.status == 200 and not s.error]
    usage_totals = _sum_usage(samples)
    # Cost from aggregate tokens across successful samples.
    cost = estimate_run_usd(usage_totals)
    n_ok = int(usage_totals.get("n_ok") or 0)
    per_ask = {
        "chat_usd": round(cost["chat_usd"] / n_ok, 6) if n_ok else 0.0,
        "embed_usd": round(cost["embed_usd"] / n_ok, 6) if n_ok else 0.0,
        "total_usd": round(cost["total_usd"] / n_ok, 6) if n_ok else 0.0,
    }
    return ScenarioReport(
        id=scenario_id,
        endpoint=endpoint,
        question=question,
        n=len(samples),
        latency=summarize_latencies(ok_lat),
        usage_totals=usage_totals,
        cost_usd_est={"aggregate": cost, "per_successful_ask": per_ask},
        samples=[asdict(s) for s in samples],
        notes=notes,
    )


def run_harness(
    *,
    base_url: str = "http://127.0.0.1:8000",
    email: str = DEFAULT_EMAIL,
    password: str = DEFAULT_PASSWORD,
    project_key: str = "ATLAS",
    ask_question: str = DEFAULT_ASK,
    multihop_question: str = DEFAULT_MULTIHOP,
    ask_repeats: int = 3,
    multihop_repeats: int = 5,
    timeout_s: float = 180.0,
) -> dict[str, Any]:
    with httpx.Client(base_url=base_url, timeout=timeout_s) as client:
        health = client.get("/health")
        if health.status_code != 200:
            raise RuntimeError(f"API unhealthy: {health.status_code} {health.text[:200]}")
        token = _token(client, email, password)

        ask_samples = measure_endpoint(
            client,
            token=token,
            endpoint="/v1/ask",
            question=ask_question,
            project_key=project_key,
            repeats=ask_repeats,
        )
        mh_samples = measure_endpoint(
            client,
            token=token,
            endpoint="/v1/multihop",
            question=multihop_question,
            project_key=project_key,
            repeats=multihop_repeats,
        )

    ask_report = build_report(
        scenario_id="ask_atlas_q2",
        endpoint="/v1/ask",
        question=ask_question,
        samples=ask_samples,
        notes="Full agent path (plan→gather→follow→probe*→finish→evidence→synthesise).",
    )
    mh_report = build_report(
        scenario_id="multihop_atlas_q2",
        endpoint="/v1/multihop",
        question=multihop_question,
        samples=mh_samples,
        notes="LLM-free gather→follow→pack baseline (no chat tokens).",
    )

    return {
        "base_url": base_url,
        "project_key": project_key,
        "email": email,
        "pricing": pricing_notes(),
        "scenarios": [asdict(ask_report), asdict(mh_report)],
        "disclaimer": (
            "Latencies are wall-clock against a live local API. "
            "USD figures are list-price estimates × measured tokens — not invoices."
        ),
    }
