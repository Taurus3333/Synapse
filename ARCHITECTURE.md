# Synapse — Architecture

Engineering guide: **flow → steps → why → example → failure → common mistake**.  
Describes the **system as built** after chunks 0–22. Deferred pieces are listed in §17 — do not claim them as shipping.

**How to learn end-to-end:** §1 ask flow → §2 LangGraph → §3 tools → §4–6 data/memory → §7 security → §8–11 quality loops → §12–16 ops → §18 design checklist.

**Package map (reconstruct from the repo):**

```text
synapse/
  api/          FastAPI routes (auth, live, ask, runs, memory, connectors, obs)
  agent/        LangGraph graph, runner, multihop planner
  tools/        ToolSession + connectors + MCP stdio server
  evidence/     assemble pack, frame DATA, ground citations
  rag/          chunk, embed, pgvector store, retrieve, ingest CLI
  memory/       STM (runs/checkpoints) + LTM (durable notes)
  auth/         JWT, passwords, principal, deps
  guardrails/   input policy, allowlists, structured parsers
  reliability/  retry, HTTP errors, DLQ-lite
  eval/         golden cases + scorer + synapse-eval
  redteam/      attacks, PyRIT converters, scorers + synapse-redteam
  obs/          metrics registry, timed spans
  perf/         usage/cost + synapse-perf harness
  e2e/          live validation + synapse-e2e
  ui/           Streamlit chat + Demo Lab
  platform/     config, db, redis, schema, seed, logging
  data/         synthetic corpus generator
  domain/       shared models/enums
  # worker/     reserved — unused (ask is sync; no SQS consumer yet)
infra/terraform AWS: VPC, ALB, ECS, RDS, Redis, ECR, Secrets, IAM
scripts/deploy  apply → ecr_push → rollout → bootstrap
docs/           demo_results.json · perf_results.json · e2e_results.json  (measured only)
```

---

## 0. One-sentence system

Synapse answers delivery questions by combining **live enterprise rows** (SQL connectors), **live public external APIs** (GitHub/HN/SO/Wikipedia; optional private Slack/Gmail), **RAG docs**, and **memory**, under **JWT tenancy**, with a **bounded LangGraph** agent whose **first cross-source hops are code-owned**, plus **citation grounding**.

Canonical question:

> “Summarize what changed in Project Atlas during Q2 and identify the major risks.”

---

## 1. End-to-end ask flow (what exists today)

```text
Browser/curl
    │  email+password
    ▼
POST /v1/auth/token  ──▶  JWT {sub, tid, role}
    │  Authorization: Bearer …
    ▼
POST /v1/ask {question, project_key: ATLAS}
    │
    ├─① verify JWT / reject spoofed X-Tenant-ID
    ├─①b input guardrail (block jailbreak / secret-exfil / cross-tenant probes)
    ├─② authorize project (membership | admin/lead)
    ├─③ STM.start_run → run_id
    ├─④ LangGraph: plan → gather → follow → probe* → finish → evidence → synthesise
    ├─⑤ structured JSON validate + ground citations (drop invented ids)
    ├─⑥ STM.complete · LTM.remember_run_summary (best-effort)
    └─⑦ JSON response {answer, citations, gaps, conflicts, hops, guardrail, run_id, …}
```

**Step ① — What happens:** Decode JWT; tenant comes from claim `tid`, not from the body.  
**Why:** A client-supplied `X-Tenant-ID` is spoofable.  
**Example:** Northwind token cannot read Globex rows.  
**Fails:** Missing/bad token → 401; spoofed header ≠ tid → 403.  
**Why:** *“Why JWT if you already filter SQL?”* — AuthN proves identity; SQL filter enforces it. You need both.

**Step ①b — What happens:** `check_user_question` hard-blocks jailbreak / secret-exfil / cross-tenant / tool-abuse / role-play escapes — including de-obfuscated views (base64, leet, character-space, flip, alnum-flat signatures). Soft flags remain for weaker educational framing.  
**Why:** Retrieved-doc injection is handled by DATA framing; *user* jailbreaks are a different channel — stop them at the door before token spend.  
**Fails:** Hard hits → HTTP 400 `guardrail_blocked` (no STM run burn).  
**Why:** *“Isn’t that just a regex toy?”* — It’s a **policy gate + de-obfuscation**, not an LLM moderator. Chunk 15 red-teams it with PyRIT converters; failures become new signatures. Honest about the limit: novel phrasing can still slip — grounding/framing/tenant SQL are the backstops.

**Step ② — What happens:** Load project by `(tenant_id, key)`; check membership unless admin/lead.  
**Why:** AuthN ≠ AuthZ. Same tenant can still lack project access.  
**Example:** Viewer may read; cannot POST risks.  
**Fails:** Unknown/cross-tenant key → **404** (no existence leak).  
**Why:** *“Why 404 not 403 for cross-tenant?”* — Avoids confirming the project exists elsewhere.

**Step ③ — What happens:** Insert `agent_runs` row; later append checkpoints.  
**Why:** Durable execution audit beats “whatever was in memory when the process died.”  
**Example:** After ask, `GET /v1/runs/{id}/checkpoints` shows plan→gather→follow→probe→evidence→done.  
**Why:** *“Why Postgres for STM instead of Redis?”* — STM must survive restarts and be queryable; Redis is volatile. Redis in Synapse is **health only** today (fast KV + TTL is the right *primitive* for cache/rate-limits later — wrong for run truth).

**Step ④ — What happens:** Bounded graph runs tools + LLM (see §2). Follow hops are **code-owned**.  
**Why:** Outer workflow is known; only residual probe branches.  
**Not here:** Queue → worker. Ask is **synchronous** today — including the Streamlit chat (each bubble is one sync `/v1/ask`).  
**Also:** `POST /v1/multihop` runs gather→follow→pack with **no LLM** for integration smoke.  
**Why:** *“Why not async/SQS?”* — Correct at scale (accept fast, work in background, DLQ poison). We deferred it; claiming async without a queue would be dishonest.
**Why:** *“Is the UI a chatbot or Q&A?”* — Chat-shaped **delivery Q&A**. Each user message starts a fresh bounded agent run for a project; history in the UI is a transcript, not an unbound multi-turn brain dump into one prompt.

**Step ⑤ — What happens:** Model proposes citations; code keeps only ids present in the EvidencePack.  
**Why:** Soft “please cite” prompts fail; closed-world validation does not.  
**Example:** Fake `rsk_FAKE` → `rejected_citations`.  
**Why:** *“Why validate after the LLM?”* — LLMs are generators; authority stays in code.

**Step ⑥–⑦ — What happens:** Persist answer on the run; optionally write LTM summary; return JSON.  
**Why:** Next ask can `memory_search`; never treat that summary as live status.  
**Fails:** Provider error → STM `failed` + HTTP 503 when appropriate.

---

## 2. LangGraph agent flow

```text
START
  │
  ▼
plan          LLM picks evidence slots
  │
  ▼
gather        CODE always pulls live baseline (project, risks, blockers, activity, tasks)
  │
  ▼
follow        CODE plans multi-hop from live signals → docs / email / meetings / memory /
              public GH · HN · SO · Wikipedia
  │
  ▼
probe ◄──┐    LLM picks ONE tool if slots still empty
  │      │
  ├─ slots empty AND budget left? ── yes ─┘
  │
  ▼ no / budget spent
finish        CODE fills remaining gaps (best-effort; skips slots follow already filled)
  │
  ▼
evidence      assemble_pack + conflict detect
  │
  ▼
synthesise    LLM writes answer from pack only → ground citations
  │
  ▼
END
```

**What “agentic” means here:** the system may choose *residual* tools under budgets. It is **not** an unbound employee with a credit card. Outer workflow is known; only the middle probe branches.

### Concepts behind this graph (plain English)

Common pattern names map to **what Synapse actually does** — claim only what the code ships.

| Pattern people say | What it is | How Synapse uses it (or doesn’t) |
|---|---|---|
| **Workflow / DAG** | Fixed steps you could draw before runtime | **Yes — outer shell:** plan→gather→follow→probe*→finish→evidence→synthesise. Prefer workflow when the flow is *known*. |
| **Agent** | Loop that *chooses* the next action from observations | **Yes — only in `probe`:** LLM picks the next allowlisted tool while slots are empty and budget remains. Not free roam. |
| **Tool use / function calling** | Model asks for a named capability; code executes it | **Yes — everywhere data is touched.** Tools carry the principal; model never gets raw SQL or tenant ids. |
| **ReAct** (Reason + Act) | Alternate “thought” text and tool calls in one free loop | **Partially.** Probe is Act-with-budget. We do **not** ship an open ReAct while-loop — hard to draw, budget, or audit. LangGraph *is* the controllable cousin. |
| **Chain-of-Thought (CoT)** | Model writes intermediate reasoning before the answer | **Lightly / not as a product feature.** Plan/probe return **structured JSON** (slots / tool), not long “let’s think step by step” essays. Reasoning that matters is **in the graph + tool trail + hops**, not hidden in prose. Temperature 0 keeps that stable. |
| **Multi-hop** | Later retrieval depends on earlier findings | **Yes.** Code-owned `follow` hops from live risks → email/docs/…; probe may add residual hops. |
| **Multi-agent** | Several agents with roles/handoffs | **No.** One question → one bounded agent. Extra agents need a second objective. |
| **RAG** | Retrieve chunks → condition generation | **Yes — one tool among many** (`document_search`), not the whole system. Live SQL beats stale chunks for status. |
| **Memory** | Sticky state across time | **STM** = this run’s checkpoints. **LTM** = durable notes. Neither replaces live rows. |

**Requirement → Problem → Primitive → Choice (orchestration):**  
Need answers that gather evidence across systems → free LLM wandering burns cost and invents steps → primitive = **state machine + tools** → choice = LangGraph with code-owned gather/follow and bounded probe → trade-off = less “autonomous magic,” more auditable demos.

**Why:** *“Do you use Chain-of-Thought?”* — We don’t market CoT as a feature. Intermediate structure is the **checklist, hops, and tool trail**. If the model narrates privately inside a token, that is not our audit surface — **STM checkpoints are**.  
**Why:** *“Is this an agent or a workflow?”* — Both: **workflow outside, agent inside the probe budget.**  
**Why:** *“Why not pure ReAct?”* — ReAct is a pattern; without caps, fingerprints, and code-first hops it becomes an unbounded bill and an undrawable story.

**Plan — What:** JSON slots from a fixed vocabulary (includes `cross_source_follow`).  
**Why:** Forces a structured checklist instead of free-form wandering — you cannot bound spend or explain hops inside one giant prompt.  
**Why:** *“Why not one giant prompt?”* — No checklist, no hop audit, no place to put a cap.

**Gather — What:** Deterministic first hops into live tables (same tools every time).  
**Why:** Live truth must not depend on the model “remembering” `risk_list`. This is also where **determinism** starts: control plane in code, not in prose.  
**Example:** ATLAS `at_risk` + open vendor blocker always enter the pack before docs.  
**Why:** *“How is an agent deterministic?”* — Separate control plane (graph, gather, follow, budgets, fingerprints) from generation (LLM prose). Two runs on the same DB should tell the same delivery *story*; wording may vary.

**Follow — What:** `plan_follow_hops` reads gather results; open risks/blockers (or at-risk/delayed) queue docs/email/meetings/memory + public GH/HN/SO/Wikipedia; else baseline doc+memory from the question.  
**Why (Requirement→Problem→Primitive→Choice):** Multi-hop must connect live risks to narrative. LLM-chosen first hops are opaque and flaky → deterministic planner over tool results → code owns the *first* wave; LLM owns *residuals*.  
**Trade-off:** Less “fully autonomous”; far more auditable (`hops[].reason`, `source_ids`).  
**Example:** ATLAS “Vendor SDK…” → Harbor SDK email + Q2 retro meeting before synthesise.  
**Why:** *“Who decides the next hop?”* — Code for the first wave from live evidence; the model only if checklist slots remain empty.  
**Common mistake:** Calling “multi-hop” when the model free-form loops with no live→narrative contract.

**Probe — What:** Up to N LLM-chosen tool calls (`max_probe_steps`, default **3** — tightened in Chunk 17 from 6 after measuring ATLAS Q2 avg ≈ 1 probe); duplicate tool+args fingerprints refused; chat at **temperature 0** for stabler JSON.  
**Why:** Agency only where the next hop depends on prior evidence *and* follow left a gap.  
**Trade-off:** Less autonomous than free ReAct; drawable, budgetable, checkpointable.  
**Why:** *“Why LangGraph instead of a while-loop / free ReAct?”* — Explicit nodes/edges are a state machine you can draw, budget, and STM-phase. A while-loop works until you must explain cost and failure clearly.  
**Why:** *“Why temperature 0?”* — Delivery status is not creative writing; stable tool JSON and calmer demos.

### Budgets: cost control vs finishing the job

Unbounded loops burn money. Blind caps can stop one hop short of the proving email. Synapse **shapes the loop** so must-have work is cheap and fixed; the uncertain part is small; a stop still returns an honest partial.

```text
Cheap + must-have     CODE: gather → follow     (predictable tool count)
Expensive + optional  LLM:  probe*              (max_probe_steps)
Always before answer  CODE: finish → evidence → synthesise
Hard ceiling          max_tool_calls (default 28)
```

| Lever | Effect |
|---|---|
| Front-load gather + follow | Cap hits optional probe first, not core live evidence |
| Checklist = “done” | Stop when slots filled **or** budget spent — not when the model “feels” done |
| finish after probe | One last deterministic fill even if probe caps out |
| gaps / conflicts in JSON | Honest partial > fake complete or infinite retry |
| Fingerprints | Same tool+args twice refused — stops cost burn |

**Why:** *“Won’t a cap mean you fail just before success?”* — Possible for residual probe hops; that’s why must-have hops are code-front-loaded and **finish** still runs. Optimize for “core truth within budget,” not “LLM may wander forever.”  
**Why:** *“Why not raise the cap until perfect?”* — Perfect is undefined for open retrieval. Caps are a product spend limit; quality comes from better first hops.  
**Why:** *“How do you know you’re done?”* — Checklist filled/partial + synthesise from the pack. Done = state-machine exit, not a vibe.  
**Why:** *“Why return gaps instead of retrying forever?”* — Gaps are a product signal (“no meetings found”); endless retry is a cost signal wearing a quality costume.  
**Common mistake:** “Runs until the objective is achieved” with no coded stop = unbounded bill. Cap with no front-loaded gather = coin-flip demo.

**Finish — What:** Code fills remaining empty slots best-effort; skips what follow already covered.  
**Evidence / synthesise — What:** Assemble pack with precedence; model must return JSON; **`parse_synthesis`** (Pydantic) validates or falls back; **`ground_citations`** drops ids not in the pack; soft output flags (secret-shaped text) become gaps. Plan/probe use the same pattern — **`parse_plan` / `parse_probe`** allowlist slots and tools so unknown probe tools never run.  
**Why we can trust the answer (same breath as synthesise):** trust is not “the model is smart” — it is what code lets leave the building (input policy + allowlisted tools + structured parse + grounding + precedence + STM).

```text
Model proposes answer + citation ids
    → ground_citations(pack) keeps only real ids
    → live ≻ external ≻ RAG ≻ LTM already in the pack prompt
    → response: answer, citations, rejected_citations, gaps, conflicts, hops, tool_trail
    → STM checkpoints the run
```

| Mechanism | Skeptic hears |
|---|---|
| Closed-world citations | Invented risk ids die |
| EvidencePack-only prose | Writer stays inside retrieved evidence (externals framed as DATA) |
| Live precedence | Green PDF cannot silently beat `at_risk` |
| hops + tool_trail + STM | Auditable why a hop ran |
| gaps | Thin evidence visible, not papered over |

**Why:** *“How do you trust an LLM answer?”* — I don’t. I trust tools + pack + grounding + audit trail. The model is a writer in a cage.  
**Why:** *“What if it ignores the pack?”* — Citations fail grounding; gaps/conflicts expose thin evidence. Golden eval (Chunk 14) regresses pack ids + live status without an LLM judge.  
**Why:** *“Why not multi-agent?”* — One user question, one objective. Extra agents need a second goal or handoff; else coordination is free complexity.

---

## 3. MCP / tools → authorized data → agent

```text
Agent (or MCP host)
    │  tool name + args  (NO tenant_id)
    ▼
ToolSession(principal from JWT / MCP env)
    │
    ├─ LIVE DB connectors (SQL at ask time)
    │     project_lookup / risk_list / blocker_list / task_search / project_activity
    │     email_search / meeting_search
    │        ──▶ Postgres WHERE tenant_id = principal.tid
    │
    ├─ RAG connector
    │     document_search ──▶ embed query → existing pgvector chunks (no reindex on ask)
    │
    ├─ PUBLIC EXTERNAL connectors (no personal tokens)
    │     github_search          ──▶ kubernetes/kubernetes issues (token optional)
    │     hn_search              ──▶ Hacker News Algolia
    │     stackoverflow_search   ──▶ Stack Exchange API
    │     wikipedia_search       ──▶ MediaWiki opensearch + extracts
    │
    ├─ OPTIONAL PRIVATE (token → workspace; else public fallback)
    │     slack_search / gmail_search
    │
    └─ memory_search / memory_write ──▶ memory_entries (tenant-scoped)
    │
    ▼
typed result → EvidencePack item (framed as DATA if text)
```

**What:** Tools are the only way the model touches systems — that *is* the connector layer.  
**Why:** Capability-scoped functions beat “here is a SQL string.”  
**Live DB connectors:** Not a separate missing product. `project_lookup`, `risk_list`, … **are** live data access: every ask hits current Postgres rows.  
**Public externals:** Heterogeneity without personal Gmail — same tool contract; `provider` + `mode` in the payload.  
**Ops:** `GET /v1/connectors` → `live_db` + `public_external` + `optional_private`; `GET /v1/connectors/ready` probes them.  
**Example:** `risk_list({project_key:"ATLAS"})` — tenant from principal, not args.  
**Fails:** Bad args → tool error; network blip → gap; personal tokens optional.  
**Why:** *“Why MCP instead of direct DB access?”* — MCP is an **interface**. Security is principal binding + SQL filters.  
**Why:** *“Why tools never take `tenant_id`?”* — Anything the model can type can be spoofed. Tenant comes from the JWT principal bound into `ToolSession`.  
**Why:** *“How do you show live externals without my Gmail?”* — Public GitHub (k8s) + HN + Stack Overflow + Wikipedia by default.

**Engineering principle:** Treat the model as an untrusted planner; treat tools as the trusted executor.

---

## 4. Live dynamic database vs RAG — do we reindex on every ask?

**Short answer: No.** Agentic RAG here is **not** “embed the whole world on every question.” Live facts and narrative docs use **different read paths**.

```text
                    ┌─────────────────────────────────────────┐
  PATCH /status     │  LIVE Postgres rows                     │
  new risk/blocker  │  projects · risks · blockers · tasks    │── SQL tools
  email/meeting row │  activity · emails · meetings           │   at ASK time
                    └─────────────────────────────────────────┘
                                      │
                                      │  never goes through embed/vector
                                      ▼
                               EvidencePack (precedence 0)

                    ┌─────────────────────────────────────────┐
  Document body     │  documents table                        │
  changes / seed    │         │                               │
                    │         ▼  synapse-ingest (offline/CLI) │
                    │  chunk → embed → document_chunks        │
                    │  (pgvector); content_hash per chunk     │
                    └─────────────────────────────────────────┘
                                      │
                                      │  document_search at ASK time
                                      │  = embed(query) + ANN only
                                      │  ≠ re-ingest corpus
                                      ▼
                               EvidencePack (precedence 10)
```

### What happens on each ask

| Event | Live DB | RAG vectors |
|---|---|---|
| `POST /v1/ask` | **SELECT** current rows via tools | **Retrieve** existing chunks; embed the *query* only |
| `PATCH` project status | Row updates immediately | **Untouched** — docs may now conflict (detected in pack) |
| New risk / blocker / email | Inserted; next ask sees it | **Untouched** |
| New/edited document | Row in `documents` | Run **`synapse-ingest`** to re-chunk/re-embed **that** corpus pass |
| Every ask | — | **Do not** wipe/rebuild the vector index |

### Requirement → Problem → Primitive → Choice → Trade-off

| | |
|---|---|
| **Requirement** | Answers must reflect *today’s* delivery state and still use long prose docs. |
| **Problem** | Embed status → PATCH invisible until reindex; SQL-only → lose semantic search over retros/PDFs. |
| **Primitive** | Two stores, one Postgres: OLTP tables + `document_chunks` (pgvector). |
| **Choice** | **Live connectors = SQL.** **RAG = documents only**, ingested when docs change. Ask cost = tool SQL + one query embedding + ANN. |
| **Trade-off** | Docs can lag live status (we surface **conflicts**). Worth it: status correct without embed cost per mutation. |

### Live vs RAG Q&A (same weight as Redis)

**Why:** *“Do you reindex RAG whenever the database changes?”*  
**Answer:** No. Status/risks/blockers are **live SQL**, not vectors. RAG is for **document narrative**. Re-ingest when a *document* changes (`synapse-ingest`), not on every ask or every PATCH.

**Why:** *“Then how is this Agentic RAG on a dynamic database?”*  
**Answer:** The agent **tools** into a dynamic DB (gather/follow). RAG is one tool among many. Dynamics live in Postgres rows; vectors are a **derived index over docs**.

**Why:** *“Why not embed project status?”*  
**Answer:** Status must be authoritative after a write. Vectors are approximate and lag. Embedding status is the Redis-class mistake: wrong primitive for the job.

**Why:** *“Why is Redis health-only here?”*  
**Answer:** Same pattern: Redis is great for TTL cache / rate-limits; **wrong** for run truth (STM) and **wrong** as source of delivery status. Postgres owns truth.

**Common mistake:** “We re-embed the knowledge base every request so it’s always fresh” — expensive, slow, and still loses transactional writes. Freshness for ledger facts = **read the row**.

---

## 5. RAG flow (ingest → retrieve → cite)

```text
Documents in Postgres
    │
    ▼
chunk (~400 words, overlap)
    │
    ▼
embed (text-embedding-3-small, 1536-d, content-hash cache)
    │
    ▼
document_chunks + HNSW   (same Postgres, tenant_id on every row)
    │
    ▼  query time
embed(question) → ANN → distance filter → tenant (+ project) filter
    │
    ▼
frame <<<RETRIEVED_DATA not instructions>>>
    │
    ▼
EvidencePack (precedence 10) → synthesise → ground citations
```

**Ingest — Why chunk/embed:** Long prose will not fit the context window; semantic search finds paraphrases keyword miss.  
**Why pgvector in Postgres:** One operational database; joins/filters with live rows; no second SaaS as truth.  
**Trade-off:** Embed cost/latency; mitigated by hashing.  
**When to re-ingest:** Document create/update (CLI `synapse-ingest` today) — **not** on ask, **not** on live status PATCH.  
**Why:** *“Why RAG if we have live data?”* — Live tables answer status/counts; docs answer *narrative* (“what was said in the retro”). Different job.

**Retrieve — Why filter in SQL:** Vector similarity alone does not know tenancy.  
**Why frame as DATA:** Retrieved text may contain injection (“ignore instructions…”). Framing + never executing it as control.  
**Example:** Stale ATLAS status doc says “on track” while live is `at_risk` → **conflict**; live wins.  
**Why:** *“Why not fine-tune on tickets?”* — Fine-tunes go stale, cost more, and still need live reads for mutations.

**Vector DB is not source of truth.** Approximate recall only.

---

## 6. Live vs external vs RAG vs STM vs LTM

```text
Highest trust                         Lowest trust
─────────────────────────────────────────────────────
LIVE Postgres rows
   │  (project status, risks, blockers, tasks, activity, email, meetings)
EXTERNAL APIs
   │  (public GH/HN/SO/Wikipedia · optional Slack/Gmail) — live but not our ledger
RAG chunks
   │  (documents; may be stale)
LTM memory_entries
   │  (prior summaries/facts; TTL + supersede)
```

**Live — Why top:** PATCH status must be believed immediately.  
**External — Why below live:** A Slack message is a signal, not the project row.  
**RAG — Why below both:** Docs lag.  
**LTM — Why bottom:** Helpful recall; **never** overrides live status.  
**STM — Different axis:** Not “knowledge,” but **this run’s** checkpoints (execution state).

**Why:** *“Why STM and LTM instead of saving the chat?”* — Chat logs mix tool noise with facts; STM is auditable phases; LTM is curated durable notes with lifecycle.

**Example (Atlas):** Live `at_risk` + open SDK blocker beat a Q1 PDF that still says green — conflict named in the answer.

---

## 7. AuthN + AuthZ + tenant isolation

```text
Login → bcrypt verify → JWT
Request → Bearer required
       → tid from token
       → SQL always AND tenant_id = tid
       → project membership / role for writes
```

**Why both AuthN and AuthZ:** Knowing *who* ≠ knowing *what they may do*.  
**Why filter in the data-access layer:** UI checks are skippable; SQL is not.  
**Why:** *“Where does identity come from?”* — Token/principal. Never from the model.

---

## 8. Failure behaviour & reliability (as built)

```text
External GET / LLM / embed
    │
    ├─ timeout bounds (httpx / provider)
    ├─ retry with exponential backoff + jitter
    │     only on 408/429/5xx and transport blips
    │     NOT on 4xx (except 408/429) — fail-fast auth/validation
    ├─ duplicate tool fingerprint → refuse (idempotent gather/follow/probe)
    ├─ missing personal token → public fallback (still live)
    ├─ empty retrieval → gap (continue)
    └─ hard exception in ask → STM.fail → appears on GET /v1/runs/failed (DLQ-lite)
                              → POST …/acknowledge after review
```

**Retries — Why only some errors:** Retrying a 401 burns quota and hides bugs. Retrying a 503 often succeeds.  
**Idempotency — Why fingerprints:** Same tool+args twice in one run is almost always a loop; refuse instead of double-billing.  
**DLQ-lite — Why not SQS yet:** Ask is still **synchronous**. Failed runs in Postgres *are* the poison queue until we add accept→worker. Claiming SQS without a queue would be dishonest.  
**Why:** *“Why timeout here?”* — One hung GitHub call must not freeze the whole ask.  
**Why:** *“Retry vs fail-fast?”* — Retry transient idempotent reads; fail-fast on auth and bad args.  
**Why:** *“Where is the DLQ?”* — `GET /v1/runs/failed` today; real SQS/DLQ when Chunk 18–20 (or async ask) lands.

---

## 9. Evaluation (golden, as built)

```text
synapse-eval (or pytest)
    │
    ▼
seed 42 / profile ci  →  ToolSession(JWT principal)
    │
    ▼
run_multihop_integration  (gather→follow→pack; NO chat model)
    │
    ▼
score_multihop(GoldenCase)
    ├─ live_status == expected?
    ├─ require_pack_ids ⊆ pack.record_ids?   → pack_id_recall
    ├─ require_hop_tools ⊆ hops?
    └─ require_sources flags true?
    │
    ▼
optional: score_answer(fixture|ask)  → citation_id_recall, forbid phrases, precision
    │
    ▼
SuiteReport: pass_rate = cases_passed / n   (only measured aggregate)
```

**Requirement:** Prove ATLAS Q2 (and HARBOR delayed) still retrieve the right live ids after refactors.  
**Problem:** LLM-as-judge invents scores and drifts; you cannot rely on a floating “0.87 quality” score.  
**Primitive:** Closed-world checks against seed-stable ids (`rsk_nw_00003`, `eml_nw_00007`, …).  
**Choice:** Gate on **multihop pack** first (deterministic). Answer scoring is fixture/ask-optional — same checks, still no judge model.  
**Trade-off:** Does not score prose elegance; that is intentional. Adversarial input is Chunk 15 (`synapse-redteam`), not retrieval regression.

**Why:** *“What’s your eval score?”* — Suite **pass rate** on golden cases (today 2/2 when DB seeded). Not BLEU, not GPT-judge.  
**Why:** *“Why not judge with another LLM?”* — Circular and non-reproducible. Seed ids either land in the pack or they don’t.

---

## 10. Red team / injection (as built)

```text
Attack corpus (jailbreak, exfil, cross-tenant, tool-abuse, RAG inject, fake cites)
    │
    ├─ PyRIT converters (deterministic leet, base64, char-space, flip)
    │     + local fallbacks if pyrit extra missing
    ▼
score_policy_block  →  check_user_question MUST deny (defense win)
score_framing        →  injected doc/comment wrapped as <<<RETRIEVED_DATA>>>
score_grounding      →  invented / foreign ids rejected by ground_citations
benign controls      →  ATLAS/HARBOR asks still ALLOWED
HTTP (integration)   →  POST /v1/ask jailbreak → 400; foreign JWT+ATLAS → 404/403
    │
    ▼
synapse-redteam → RedTeamReport.pass_rate   (only measured aggregate)
```

**Requirement:** Prove the cage holds under *hard* injection, not just the happy path.  
**Problem:** A regex that only matches “ignore previous instructions” loses to Base64/leet/spacing; an LLM judge invents “safe.”  
**Primitive:** PyRIT **converters** mutate seeds; Synapse scorers are deterministic (block / frame / reject).  
**Choice:** Gate CI on defense hold-rate — not on “model refused nicely.” Planted ATLAS injection document + comment exist in seed-42 for framing tests.  
**Trade-off:** We use PyRIT converters (optional extra `.[redteam]`), not full multi-turn Crescendo against live Groq in CI — that would be flaky/costly. Live ask adversarial runs are the next hardening loop, not a fake score.

**Why:** *“Did you red-team or just unit-test regex?”* — Both: PyRIT mutations + HTTP ask block + framing + grounding + cross-tenant 404.  
**Why:** *“What if injection is in a retrieved doc?”* — User channel ≠ data channel. Docs are DATA-framed; synthesise + grounding still can’t mint foreign ids.

---

## 11. Observability (as built)

```text
HTTP request
    │  X-Correlation-ID (echo / mint)
    ▼
middleware  →  http_requests_total + http_request_duration_ms
            →  X-Request-Duration-Ms on response
            →  structlog http_request {route, status, duration_ms, correlation_id}
    │
    ├─ POST /v1/ask
    │     timed_span("ask") + asks_total{outcome} + ask_duration_ms
    │     tool_calls_total / tool_duration_ms per tool (via call_tool)
    │     STM checkpoints = durable phase audit (plan/gather/follow/…)
    │
    └─ GET /v1/metrics  →  process snapshot (counters + timing summaries)
```

**Requirement:** Reconstruct one ask from logs and know aggregate health without a SaaS bill.  
**Problem:** LangSmith (or full OTel→Jaeger) is tempting next to LangGraph, but Synapse already persists run truth in STM + tool_trail. A second vendor trail duplicates cost and drifts from what `/v1/runs` shows.  
**Primitive:** Correlation ID + structured spans + in-process counters/summaries.  
**Choice:** **No LangSmith.** Ship `GET /v1/metrics` and span logs. Prometheus scrape / OTel export is the honest upgrade when multi-instance AWS lands.  
**Trade-off:** Metrics reset on process restart; fine for local/demo, not a multi-node TSDB.

**Why:** *“Where’s LangSmith?”* — Deliberately skipped. STM checkpoints are the agent audit; metrics cover aggregates; LangSmith would be a parallel story that can disagree with Postgres.  
**Why:** *“How do you debug a bad answer?”* — `correlation_id` → logs → `run_id` → STM checkpoints + hops + rejected_citations.

---

## 12. Performance & cost (as built)

```text
synapse-perf ──▶ live /v1/ask + /v1/multihop
                    │
                    ├─ wall latency  → p50 / p95 / mean (summarize_latencies)
                    ├─ chat tokens   → UsageAccumulator (plan / probe / synthesise)
                    ├─ embed tokens  → Embedder (content-hash cache hits counted)
                    └─ cost_usd      → list-price × measured tokens (estimate, not invoice)
                              │
                              ▼
                    docs/perf_results.json   + per-ask usage in /v1/ask response
```

**Requirement:** Know what an ask costs and how long it takes — without inventing an SLA.  
**Problem:** Budgets without measurements are superstition; premature “optimizations” (skip probe, cache answers) fight live-first correctness.  
**Primitive:** Token accumulator on every chat/embed call + HTTP harness against the same API the UI uses.  
**Choice:** Measure first (`synapse-perf`). Tighten `max_probe_steps` 6→3 because measured ATLAS Q2 averaged **1** probe step — not because theory said so. Did **not** skip probe entirely: residual agency still earns its one call. Did **not** cache ask answers: live PATCH demos would lie.  
**Trade-off:** Numbers are local wall-clock + list-price estimates. They support design choices; they are not a published multi-region SLA.

### Measured (local, seed-42 ATLAS Q2 — `docs/perf_results.json`)

| Path | n | p50 | p95 | avg chat calls | avg probe | avg tokens | est USD/ask |
|---|---|---|---|---|---|---|---|
| `/v1/ask` | 3 | **23.6 s** | **45.3 s** | **3.0** | **1.0** | **3866** | **~$0.00095** |
| `/v1/multihop` | 5 | **8.7 s** | **9.4 s** | 0 | — | 0 chat | ~$0 |

Pricing assumptions (see report): Groq `openai/gpt-oss-20b` ~$0.10/$0.50 per MTok in/out; `text-embedding-3-small` ~$0.02 per MTok. Embed spend on ask is noise (~6 tokens/query).

**Why:** *“What’s your p50?”* — ~24 s for full ask on this machine/corpus; ~9 s for LLM-free multihop. Not an SLA.  
**Why:** *“How did you cut cost?”* — Front-loaded gather/follow so probe averages one step; then lowered the cap to 3. Token accounting proved the graph was already cheap before we touched it.  
**Why:** *“Why not cache the answer?”* — Live status can PATCH between asks; answer cache would demo a lie.

---

## 13. Local topology (actual)

```text
Internet (dev machine)
    → Streamlit :8501          chat UI (each bubble → POST /v1/ask)
    → FastAPI :8000
         → Postgres :5432 (+vector)   live OLTP + RAG chunks + STM + LTM
         → Redis :6379                health ping only today
         → Groq / OpenAI (chat + embeddings)
         → public GH / HN / SO / Wikipedia
         → optional Slack / Gmail tokens
```

Compose starts Postgres+Redis. AWS Terraform lives under `infra/terraform/` (Chunk 18).

---

## 14. AWS topology (Terraform — as built)

```text
Internet
   │
   ▼
ALB :80  (public subnets)     ← HTTPS/ACM/Route53 = Chunk 19 polish
   │
   ▼
ECS Fargate API (private)     ← image from ECR; secrets from Secrets Manager
   │
   ├─ RDS Postgres 16 (private, encrypted, SSL)   + pgvector extension post-boot
   └─ ElastiCache Redis 7 (private)               health ping (same contract as local)

CloudWatch Logs  /ecs/synapse-*/api
IAM              execution role (ECR + secrets) · task role (logs only)
NAT Gateway      private → internet (ECR pull, Groq/OpenAI)
```

**Requirement:** Reproducible cloud shape for the *same* single API process that runs locally.  
**Problem:** Kubernetes / microservices / SQS look impressive and add nothing until async ask or multi-service handoff exists.  
**Primitive:** VPC + ALB + one Fargate service + managed Postgres/Redis + Secrets Manager.  
**Choice:** Terraform under `infra/terraform/`. `api_desired_count` defaults to **0** until an image is pushed (Chunk 19).  
**Trade-off:** No `terraform apply` in CI without AWS credentials — `terraform validate` is the Chunk 18 gate.

| Included | Why |
|---|---|
| VPC public/private + NAT | API not on the public internet; still reaches LLM APIs + ECR |
| ALB + `/health` | Same health contract as local |
| ECS Fargate | One container = one FastAPI process — matches the product |
| ECR | Image repository for the Dockerfile |
| RDS Postgres 16 | Live OLTP + pgvector (enable `vector` after first connect) |
| ElastiCache Redis | App already requires `SYNAPSE_REDIS_URL` |
| Secrets Manager | JWT / LLM keys / DB URL — never in git |
| CloudWatch Logs | Replace local stdout for the task |

| Omitted on purpose | Why |
|---|---|
| SQS + SQS DLQ | Ask is sync; failed runs already in Postgres (`/v1/runs/failed`) |
| S3 | No upload/artifact pipeline yet |
| CloudFront / Route53 | Optional custom domain — ALB DNS works; ACM ARN wires HTTPS |
| X-Ray / OTel collector | Chunk 16 chose correlation + `/v1/metrics` |
| EKS / Lambda sprawl | No second service to justify |

**Why:** *“Why Fargate not EKS?”* — One API container. k8s is an ops product we do not need.  
**Why:** *“Where’s the queue?”* — Not built. Sync ask + STM failure list until async is a real requirement.  
**Why:** *“How do secrets stay out of git?”* — `TF_VAR_*` / Secrets Manager JSON; `.tfvars` gitignored.

### Deploy sequence (Chunk 19)

```powershell
# secrets via env — never committed
$env:TF_VAR_jwt_secret = "<≥32 chars>"
$env:TF_VAR_groq_api_key = "<key>"

.\scripts\deploy\apply.ps1            # terraform apply (billable)
.\scripts\deploy\ecr_push.ps1         # docker build + ECR push
.\scripts\deploy\rollout.ps1 -DesiredCount 1
.\scripts\deploy\bootstrap.ps1        # pgvector + seed + ingest runbook
curl "$(terraform -chdir=infra/terraform output -raw api_url)/health"
```

Optional HTTPS: set `acm_certificate_arn` to an ACM cert in the same region → ALB :443 + HTTP→HTTPS redirect.  
Prod process refuses `dev-only` JWT, localhost bind, and missing chat keys (`Settings._prod_hardening`).

---

## 15. CI/CD gates (as built)

```text
PR / push main
    │
    ├─ lint + mypy
    ├─ pytest (unit + integration, pgvector Postgres + Redis)
    ├─ synapse-eval          ← golden pack ids / live status (no LLM judge)
    ├─ synapse-redteam       ← PyRIT converters + policy/framing/grounding
    ├─ pip-audit             ← dependency vulns (skip editable self)
    ├─ terraform fmt/validate
    └─ docker build
         │
         ▼
      job ci-ok (needs all)

workflow_dispatch deploy.yml
    ├─ docker build → artifact
    ├─ optional ECR push   (AWS secrets)
    └─ optional terraform plan (AWS secrets) — never auto-apply
```

**Requirement:** Broken retrieval, broken tenancy defense, or broken infra syntax must fail the merge — not a human checklist.  
**Problem:** A single `pytest -q` job hides which gate failed; auto-deploying Terraform from every push spends money and risks prod.  
**Primitive:** Parallel GitHub Actions jobs + a manual deploy workflow.  
**Choice:** Eval and red-team are **first-class jobs** (same CLIs as local). Deploy is `workflow_dispatch` only.  
**Trade-off:** No continuous delivery to ECS yet — push image/plan when an operator opts in. Rollback = redeploy previous ECR tag via `rollout.ps1`.

**Why:** *“What’s your release gate?”* — `ci-ok`: lint, tests, golden eval, red-team, audit, terraform validate, image build, **e2e**.  
**Why:** *“Do PRs auto-deploy?”* — No. Billable AWS stays behind `deploy.yml` inputs + secrets.

---

## 16. E2E & failure validation (as built)

```text
pytest -m e2e  (CI)
    TestClient + seeded Postgres
    ├─ happy: auth → PATCH live status → multihop reflects it → metrics
    └─ fail:  jailbreak 400 · cross-tenant 404 · bad JWT 401
              · DLQ fail+ack · tool budget RuntimeError · Redis-down health 503

synapse-e2e   (operator / prod-like)
    live HTTP → docs/e2e_results.json
    same spine + optional /v1/ask when chat key present
```

**Requirement:** Prove the product path and the failure path — not only unit green.  
**Problem:** Demo scenarios alone are operator-run; CI needs a deterministic ASGI suite; prod needs a single command against the ALB.  
**Primitive:** Shared case list — ASGI for merge gate, HTTP harness for live.  
**Choice:** Keep `/v1/ask` optional in live harness (503 → skip-pass when no model); never fake a grounded ask.  
**Trade-off:** ASGI e2e does not burn Groq tokens; live e2e does when the key is set.

**Measured:** ASGI **5/5**; live **9/9** (`docs/e2e_results.json`).

**Why:** *“What did you break on purpose?”* — Redis ping, tool budget, jailbreak, cross-tenant, bad JWT, injected STM failure → DLQ.  
**Why:** *“How do you validate prod?”* — `synapse-e2e --api https://…` after rollout; same JSON artifact shape as local.

---

## 17. Deferred / non-goals — do not claim as shipping

These are **conscious omissions**, not forgotten todos:

| Item | Status | Why deferred |
|---|---|---|
| `terraform apply` / always-on ECS | Scripts + plan ready; apply is operator-run | Billable NAT/ALB/RDS — don’t auto-spend in CI |
| SQS + async ask worker | Not built | Sync ask + Postgres DLQ-lite (`/v1/runs/failed`) covers the current product path |
| LangSmith / full OTel→Jaeger | Not used | STM checkpoints already audit the agent run |
| Prometheus multi-node TSDB | Not built | In-process `/v1/metrics` is enough for single instance |
| Answer / semantic result cache | Explicitly rejected | Would disagree with live PATCH demos |
| EKS / Lambda microservices | Rejected | One FastAPI process |
| Auto-ingest webhook on every doc write | Manual `synapse-ingest` | Enough until write path is a product surface |
| Published multi-region latency SLA | Not claimed | Local p50 in `docs/perf_results.json` only |

Everything else in §§1–16 is **as built** and covered by tests, CLIs, or measured JSON artifacts.

---

## 18. Design checklist

1. JWT tenant → SQL filter → tools without tenant args.  
2. Input guardrail before spend; structured parse on plan/probe/answer.  
3. LangGraph: gather → follow (code) → probe (bounded LLM) → finish → ground.  
4. Caps vs quality live *inside* that graph: front-load must-have hops; probe capped; finish + gaps.  
5. Trust = pack + grounding + precedence + STM — not “trust the model.”  
6. Live SQL ≠ RAG; do not reindex vectors on every ask or PATCH.  
7. Precedence: live ≻ external ≻ RAG ≻ LTM.  
8. STM = run audit; LTM = durable notes; Redis ≠ truth.  
9. Public externals + `/v1/connectors/ready`.  
10. Sync ask + DLQ-lite (`/v1/runs/failed`); full queue is the next reliability step.  
11. UI is chat-shaped Q&A — each message is one bounded `/v1/ask` turn.  
12. Golden eval = seed ids in pack + live status; pass_rate only — no LLM judge.  
13. Red team = PyRIT mutations must be **blocked/framed/ungrounded**; benign asks still pass.  
14. Obs = correlation_id + spans + `/v1/metrics`; STM beats LangSmith for this design.  
15. Measure before optimize — `synapse-perf` tokens/latency; tighten budgets from evidence.  
16. AWS = one Fargate API behind ALB + RDS/Redis — not EKS, not SQS until async exists.  
17. Deploy = apply → ecr_push → rollout → bootstrap; prod Settings refuse dev JWT / localhost.  
18. Merge gate = `ci-ok` (eval + redteam + audit + terraform + image + e2e); deploy is manual.  
19. Break it on purpose — budget, Redis, jailbreak, cross-tenant — then show the JSON proof.  
20. Docs split: README runs/demos · ARCHITECTURE explains design · PROJECT narrates — no invented metrics.  
