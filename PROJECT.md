# Synapse — Engineering Journal

Companion to `README.md` (project overview) and `ARCHITECTURE.md` (how to reconstruct the system).

**Rule:** no invented metrics. Numbers below come from `docs/*.json`, CLI exits, or pytest.

---

## Final system (Chunk 22)

Synapse is a **complete** Agentic RAG product over a seedable multi-tenant work graph:

- Live enterprise SQL + optional public/private externals + RAG + STM/LTM  
- Bounded LangGraph with **code-owned first hops** and residual LLM probe  
- JWT tenancy, guardrails, grounded citations, retries, DLQ-lite  
- Golden eval, PyRIT red-team, obs (`/v1/metrics`), measured perf/cost  
- Terraform AWS topology + deploy scripts + CI gates + E2E/failure harness  
- Streamlit Demo Lab with **13/13** UI scenarios validated against the real API  

**Still operator-opt-in (not fake-complete):** paying for `terraform apply` / ECS; async SQS worker; SaaS tracing.

---

## Arc (0 → 22)

| Chunks | Theme | Outcome |
|---|---|---|
| 0–1 | Plan + repo foundation | Package layout, typed `SYNAPSE_*` config, compose |
| 2–3 | Domain + live DB | Seedable corpus; mutable Postgres APIs |
| 4–5 | RAG + MCP tools | pgvector ingest; tools bound to principal |
| 6–9 | Auth, evidence, STM, LTM | JWT tenancy; pack+grounding; run audit; durable notes |
| 10–11 | LangGraph + multi-hop | `plan→gather→follow→probe*→finish→evidence→synthesise` |
| 12–13 | Guardrails + reliability | Input policy; structured parse; retries; DLQ-lite |
| 14–15 | Eval + red-team | Golden **2/2**; PyRIT **1.0**; hardened policy |
| 16–17 | Obs + perf | Correlation + `/v1/metrics`; ask p50 **23.6s** · ~**$0.001**/ask |
| 18–19 | AWS | Terraform VPC/ALB/Fargate/RDS/Redis; deploy scripts; prod hardening |
| 20–21 | CI + E2E | `ci-ok` gates; ASGI e2e **5/5**; live e2e **9/9** |
| 22 | Docs | This final pass — one coherent story across three docs |

---

## Chunk notes (decisions that matter)

### 14–15 — Eval & red-team

Golden cases pin seed-42 ids (`rsk_nw_00003`, `eml_nw_00007`, …) via LLM-free multihop scoring — **no LLM judge**. Red-team mutates jailbreak/exfil/cross-tenant/injection seeds with PyRIT converters; scorers are deterministic (block / frame / unground / allow-benign). Naive phrase regex lost to leet until alnum-flat signatures.

### 16 — Observability without LangSmith

STM checkpoints + hops + tool_trail are the agent audit. Adding LangSmith would create a second story that can disagree with Postgres. Ship `X-Correlation-ID`, span logs, `GET /v1/metrics`.

### 17 — Measure before optimize

`UsageAccumulator` on chat+embeds; `synapse-perf` → `docs/perf_results.json`. ATLAS Q2 measured avg **1** probe step → tighten `max_probe_steps` **6→3**. Did **not** skip probe or cache answers (live PATCH demos would lie).

### 18–19 — AWS without ceremony

One Fargate API behind ALB. Omitted SQS/S3/EKS/X-Ray on purpose. Deploy is `apply → ecr_push → rollout → bootstrap`. Prod `Settings` rejects `dev-only` JWT and localhost bind. `terraform plan` against the real account: **45 to add** — apply left to the operator (billable).

### 20–21 — Gates that match local CLIs

CI jobs call the same `synapse-eval` / `synapse-redteam` humans run. E2E is two layers: ASGI for merge green, `synapse-e2e` for ALB/prod-like HTTP including failure injection.

### 22 — Documentation roles locked

| Doc | Job |
|---|---|
| README | Run it, demo it, cite measured facts |
| ARCHITECTURE | Reconstruct it; Requirement→Problem→Primitive→Choice→Trade-off |
| PROJECT | How we got here; what broke; what we measured |

---

## Decisions that stuck

- Live Postgres > vectors for status; RAG is narrative documents only.  
- MCP/tools never take `tenant_id` — principal is bound at session start.  
- EvidencePack + `ground_citations` over “please cite.”  
- STM in Postgres; Redis is health (and future cache), not run truth.  
- LTM lowest precedence — never overrides live status.  
- Public externals by default; personal tokens optional.  
- First cross-source hops are **code-owned**; LLM probes leftovers only.  
- Eval/red-team = closed-world pass rates — no LLM-as-judge scores.  
- Measure before optimize; budgets come from harness evidence.  
- Skip products that don’t earn their complexity (LangSmith, EKS, answer cache, auto-apply).

---

## Failures (worth remembering)

| Failure | Fix |
|---|---|
| Groq `tool_use_failed` on JSON tool-shaped replies | Recover `failed_generation` payload in `_chat` |
| Synthesise 413 TPM | Compact `EvidencePack.to_prompt` |
| RAG 0 hits on “current” only | Include non-current/stale chunks; surface conflicts |
| PyRIT leet bypassed naive regex | De-obfuscation + alnum-flat signatures |
| False “chunk done” after thin demos | UI-first scenarios + JSON evidence required |
| Docs claimed AWS/SLA early | Status tables only cite measured artifacts |

---

## Measured scoreboard

| | |
|---|---|
| Corpus (seed 42) | ci **441** · full **26,598** |
| Chat / embed | Groq `openai/gpt-oss-20b` · `text-embedding-3-small` |
| Golden eval | **2/2** |
| Red-team | **pass_rate 1.0** |
| UI demos | **13/13** (`docs/demo_results.json`) |
| Ask p50 / p95 | **23.6 s / 45.3 s** (`docs/perf_results.json`) |
| Ask cost (est.) | **~$0.00095**/ask · avg **3** chat · avg **1** probe |
| Multihop p50 | **8.7 s** |
| Live E2E | **9/9** (`docs/e2e_results.json`) |
| ASGI E2E | **5/5** |
| Terraform | validate OK · plan **45 add** (not applied in-repo) |
| Obs | `/v1/metrics` · **no LangSmith** |

---

## Earlier chunks (compressed)

**0–1** Confirmed architecture against the source brief; typed settings; compose Postgres+Redis; reserved namespaces.  
**2–3** Deterministic generator (`ci`/`full`); live project/risk/blocker/email/meeting APIs.  
**4–5** Chunk/embed/pgvector; ToolSession + MCP stdio; connectors = the data plane.  
**6–9** JWT + membership authz (cross-tenant **404**); EvidencePack + framing + grounding; STM runs/checkpoints; LTM notes with lowest precedence.  
**10–13** LangGraph shell; `plan_follow_hops`; input policy + Pydantic structured I/O; retries + fingerprints + failed-run DLQ-lite.  
**UI** Streamlit chat + Demo Lab — every bubble is one bounded `/v1/ask`.

---

## Definition of done (honest)

| Original expectation | Status |
|---|---|
| 2,000+ heterogeneous records | Yes — full profile **26,598**; ci **441** for tests |
| Live mutable enterprise data | Yes |
| MCP + embeddings + vector DB | Yes |
| Agentic RAG + multi-hop | Yes — bounded graph + code follow |
| STM / LTM + live precedence | Yes |
| Tenant isolation + authz | Yes — tested |
| Grounded citations + guardrails | Yes |
| Eval + PyRIT + security regression | Yes — in CI |
| Observability | Yes — correlation + metrics (no LangSmith) |
| Latency + cost measured | Yes — local artifacts |
| Terraform + deploy path | Yes — validate/plan/scripts; apply operator-run |
| GitHub Actions gates | Yes |
| README / PROJECT / ARCHITECTURE with real results | Yes (this pass) |
| Async queue / multi-region SLA / auto-apply | **No** — documented non-goals |

Chunk 22 closes the doc loop. The product you can run, demo, and measure is the one described here — not a future slide.
