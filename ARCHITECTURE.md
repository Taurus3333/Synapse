# Synapse — Architecture

**CORE — Project 4:** Go/no-go brief for the Northwind Atlas platform cutover.  
**PRODUCTION — Project 5:** Guardrails, citation checks, tenant isolation, eval, red team, CI on that same ask path.  
**ENGINEERING:** Persistence, retries, metrics, Docker Compose — surrounds the product; is not the product.

```text
USER QUESTION
      ↓
AUTH / TENANT
      ↓
BOUNDED AGENT  (code owns first hops; model fills gaps under a cap)
      ↓
┌──────────────┬──────────────┬──────────────┐
│ LIVE DATA    │ RAG          │ MEMORY       │
│ Postgres +   │ pgvector     │ STM = this   │
│ public APIs  │ documents    │ run · LTM =  │
│              │              │ durable notes│
└──────────────┴──────────────┴──────────────┘
      ↓
EVIDENCE + CITATION CHECK  (+ Project 5 guards)
      ↓
GROUNDED ANSWER
      ↓
UI EXECUTION VIEW (structured metadata — not hidden chain-of-thought)
  plan → hops [LIVE|RAG|LTM|EXTERNAL] → evidence summary → guards → answer
```

Canonical ask: *“Can the Atlas platform cutover still make the 30 June code freeze? What changed in Q2, what is the vendor SDK blocker, and does the status report agree with live status?”*  
Company rows are the Northwind cutover in Postgres (Atlas depends on Harbor Identity's vendor SDK). Live web at ask time is Hacker News, Stack Overflow, and Tavily. Globex and Initech exist so another company cannot read this cutover.

Engineering guide below: **flow → steps → why → example → failure → common mistake**.  
**How to learn:** §1–6 = Project 4 → §7–11 = Project 5 → §12–16 = topology / CI / proof / skips / checklist.

**Package map (reconstruct from the repo):**

```text
synapse/
  api/          FastAPI routes (auth, live, ask, runs, memory, connectors, obs)
  agent/        LangGraph graph, runner, multihop planner
  tools/        ToolSession + connectors + MCP stdio server
  evidence/     assemble pack, frame DATA, ground citations
  rag/          chunk, embed, pgvector store, retrieve, ingest CLI
  memory/       STM (runs/checkpoints) + LTM (durable notes)
  auth/         JWT, passwords, logged-in user, deps
  guardrails/   input policy, allowlists, structured parsers
  reliability/  retry, admission, chat circuit, ask deadline, failed-ask list
  eval/         golden cases + scorer + synapse-eval
  redteam/      attacks, PyRIT converters, scorers + synapse-redteam
  obs/          metrics registry, timed spans
  perf/         usage/cost + synapse-perf test runner
  e2e/          live validation + synapse-e2e
  ui/           Streamlit chat + Demo Lab
  platform/     config, db, redis, schema, seed, logging
  data/         synthetic data generator
  domain/       shared models/enums
docs/           demo_results.json · perf_results.json · e2e_results.json  (measured only)
```

**Plain words used in this doc**

| Term | Means |
|---|---|
| STM | Short-term run log (steps for one ask) |
| LTM | Long-term notes (saved facts; never beat live status) |
| Trust order | Which source wins: live DB → web APIs → doc search → notes |
| Failed-ask list | Broken asks stored in Postgres (`/v1/runs/failed`) |
| Answer cache | **Not used** — would disagree with live status PATCH. Embed content-hash cache only. |

---

## 0. One-sentence system

**Project 4:** Synapse writes the Atlas cutover go/no-go by combining **today's project row**, **the status report**, **mail and meetings**, and **notes**, with a **hard-limited** agent whose **first hops are owned by code**, then a grounded answer with citation checks.  
**Project 5:** The same path, with input guards, tenant isolation, eval, and red team so that brief is safe to demo and merge.  
**Engineering:** Retries, metrics, Compose — so the above is measurable and runnable locally.

Canonical question:

> “Can the Atlas platform cutover still make the 30 June code freeze? What changed in Q2, what is the vendor SDK blocker, and does the status report agree with live status?”

Recorded latency in `docs/perf_results.json` used a shorter wording of this same cutover (`ask_atlas_q2`). That file was not re-measured for the longer question.

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
    │  UI uses POST /v1/ask/stream (same ask). Events are node names and counts.
    │
    ├─① verify JWT / reject spoofed X-Tenant-ID
    ├─①b input guardrail (block jailbreak / secret-exfil / cross-tenant probes)
    ├─② authorize project (membership | admin/lead)
    ├─③ STM.start_run → run_id
    ├─④ LangGraph: plan → gather → follow → probe* → finish → evidence → write answer
    ├─⑤ structured JSON validate + ground citations (drop invented ids)
    ├─⑥ STM.complete · LTM.remember_run_summary (best-effort)
    └─⑦ JSON {answer, plan, checklist, hops, evidence{live_status,by_kind,…},
               citations, rejected_citations, gaps, conflicts, guardrail, usage, run_id}
```

**Step ① — What happens:** Decode JWT; tenant comes from claim `tid`, not from the body.  
**Why:** A client-supplied `X-Tenant-ID` is spoofable.  
**Example:** Northwind token cannot read Globex rows.  
**Fails:** Missing/bad token → 401; spoofed header ≠ tid → 403.  
**Why:** *“Why JWT if you already filter SQL?”* — Login proves who you are; SQL filter enforces it. You need both.

**Step ①b — What happens:** `check_user_question` hard-blocks jailbreak / secret-exfil / cross-tenant / tool-abuse / role-play escapes — including de-obfuscated views (base64, leet, character-space, flip, alnum-flat signatures). Soft flags remain for weaker educational framing.  
**Why:** Retrieved-doc injection is handled by DATA framing; *user* jailbreaks are a different channel — stop them at the door before token spend.  
**Fails:** Hard hits → HTTP 400 `guardrail_blocked` (no STM run burn).  
**Why:** *“Isn’t that just a regex toy?”* — It’s a **policy gate + de-obfuscation**, not an LLM moderator. Chunk 15 red-teams it with PyRIT converters; failures become new signatures. Honest about the limit: novel phrasing can still slip — grounding/framing/tenant SQL are the backstops.

**Step ② — What happens:** Load project by `(tenant_id, key)`; check membership unless admin/lead.  
**Why:** Login check ≠ permission check. Same company can still lack project access.  
**Example:** Viewer may read; cannot POST risks.  
**Fails:** Unknown/cross-tenant key → **404** (no existence leak).  
**Why:** *“Why 404 not 403 for cross-tenant?”* — Avoids confirming the project exists elsewhere.

**Step ③ — What happens:** Insert `agent_runs` row; later append checkpoints.  
**Why:** Durable execution audit beats “whatever was in memory when the process died.”  
**Example:** After ask, `GET /v1/runs/{id}/checkpoints` shows plan→gather→follow→probe→evidence→done.  
**Why:** *“Why Postgres for STM instead of Redis?”* — STM must survive restarts and be queryable; Redis is volatile. Redis in Synapse is **health only** today (fast KV + TTL is the right building block for cache/rate-limits later — wrong for run truth).

**Step ④ — What happens:** Bounded graph runs tools + LLM (see §2). Follow hops are **code-owned**.  
**Why:** Outer workflow is known; only residual probe branches.  
**Not here:** Queue → worker. Ask is **synchronous** today — including the Streamlit chat (each bubble is one sync `/v1/ask`).  
**Also:** `POST /v1/multihop` runs gather→follow→pack with **no LLM** for integration smoke.  
**Why:** *“Why not async/SQS?”* — Right at big scale (accept fast, work in background, keep a failed-job list). We skipped it for now; claiming a queue without building one would be dishonest.
**Why:** *“Is the UI a chatbot or Q&A?”* — Chat-shaped **delivery Q&A**. Each user message starts a fresh hard-limited agent run for a project; history in the UI is a transcript, not an endless multi-turn brain dump into one prompt.

**Step ⑤ — What happens:** Model proposes citations; code keeps only ids present in the EvidencePack.  
**Why:** Soft “please cite” prompts fail; check against IDs we already fetched does not.  
**Example:** Fake `rsk_FAKE` → `rejected_citations`.  
**Why:** *“Why validate after the LLM?”* — LLMs are generators; authority stays in code.

**Step ⑥–⑦ — What happens:** Persist answer on the run; optionally write LTM summary; return JSON including `evidence` counts by source kind and `guardrail` (input allowed + citation check).  
**Why:** Next ask can `memory_search`; never treat that summary as live status. The UI renders an **Execution** view from this payload — plan, lane-tagged hops, evidence summary, guards — then the answer. That is structured run metadata, not private model chain-of-thought.  
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
              Hacker News · Stack Overflow · Tavily
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
write answer    LLM writes answer from pack only → ground citations
  │
  ▼
END
```

**What “agentic” means here:** the system may choose *residual* tools under budgets. It is **not** an unbound employee with a credit card. Outer workflow is known; only the middle probe branches.

### Concepts behind this graph (plain English)

Common pattern names map to **what Synapse actually does** — claim only what the code ships.

| Pattern people say | What it is | How Synapse uses it (or doesn’t) |
|---|---|---|
| **Workflow / DAG** | Fixed steps you could draw before runtime | **Yes — outer shell:** plan→gather→follow→probe*→finish→evidence→write answer. Prefer workflow when the flow is *known*. |
| **Agent** | Loop that *chooses* the next action from observations | **Yes — only in `probe`:** LLM picks the next allowlisted tool while slots are empty and budget remains. Not free roam. |
| **Tool use / function calling** | Model asks for a named capability; code executes it | **Yes — everywhere data is touched.** Tools carry the logged-in user; model never gets raw SQL or tenant ids. |
| **ReAct** (Reason + Act) | Alternate “thought” text and tool calls in one free loop | **Partially.** Probe is Act-with-budget. We do **not** ship an open ReAct while-loop — hard to draw, budget, or audit. LangGraph *is* the controllable cousin. |
| **Chain-of-Thought (CoT)** | Model writes intermediate reasoning before the answer | **Lightly / not as a product feature.** Plan/probe return **structured JSON** (slots / tool), not long “let’s think step by step” essays. Reasoning that matters is **in the graph + tool trail + hops**, not hidden in prose. Temperature 0 keeps that stable. |
| **Multi-hop** | Later retrieval depends on earlier findings | **Yes.** Code-owned `follow` hops from live risks → email/docs/…; probe may add residual hops. |
| **Multi-agent** | Several agents with roles/handoffs | **No.** One question → one hard-limited agent. Extra agents need a second objective. |
| **RAG** | Retrieve chunks → condition generation | **Yes — one tool among many** (`document_search`), not the whole system. Live SQL beats stale chunks for status. |
| **Memory** | Sticky state across time | **STM** = this run’s checkpoints. **LTM** = durable notes. Neither replaces live rows. |

**Requirement → Problem → Primitive → Choice (orchestration):**  
Need answers that gather evidence across systems → free LLM wandering burns cost and invents steps → building block = **state machine + tools** → choice = LangGraph with code-owned gather/follow and capped probe → trade-off = less “autonomous magic,” more auditable demos.

**Why:** *“Do you use Chain-of-Thought?”* — We don’t market CoT as a feature. Intermediate structure is the **checklist, hops, and tool trail**. If the model narrates privately inside a token, that is not our audit surface — **STM checkpoints are**.  
**Why:** *“Is this an agent or a workflow?”* — Both: **workflow outside, agent inside the probe budget.**  
**Why:** *“Why not pure ReAct?”* — ReAct is a pattern; without caps, fingerprints, and code-first hops it becomes an unlimited spend and an undrawable story.

**Plan — What:** JSON slots from a fixed vocabulary (includes `cross_source_follow`).  
**Why:** Forces a structured checklist instead of free-form wandering — you cannot bound spend or explain hops inside one giant prompt.  
**Why:** *“Why not one giant prompt?”* — No checklist, no hop audit, no place to put a cap.

**Gather — What:** Repeatable first hops into live tables (same tools every time).  
**Why:** Live truth must not depend on the model “remembering” `risk_list`. This is also where **determinism** starts: control plane in code, not in prose.  
**Example:** ATLAS `at_risk` + open vendor blocker always enter the pack before docs.  
**Why:** *“How is an agent repeatable?”* — Separate control plane (graph, gather, follow, budgets, fingerprints) from generation (LLM prose). Two runs on the same DB should tell the same delivery *story*; wording may vary.

**Follow — What:** `plan_follow_hops` reads gather results; open risks/blockers (or at-risk/delayed) queue docs/email/meetings/memory + Hacker News, Stack Overflow, and Tavily; else baseline doc+memory from the question.  
**Why (Requirement→Problem→Primitive→Choice):** Multi-hop must connect live risks to narrative. LLM-chosen first hops are opaque and flaky → repeatable planner over tool results → code owns the *first* wave; LLM owns *residuals*.  
**Trade-off:** Less “fully autonomous”; far more auditable (`hops[].reason`, `source_ids`).  
**Example:** Open risk “Vendor SDK miss…” → Harbor SDK email + Q2 retro before the answer is written.  
**Why:** *“Who decides the next hop?”* — Code for the first wave from live evidence; the model only if checklist slots remain empty.  
**Common mistake:** Calling “multi-hop” when the model free-form loops with no live→narrative contract.

**Probe — What:** Up to N LLM-chosen tool calls (`max_probe_steps`, default **3** — tightened in Chunk 17 from 6 after measuring ATLAS Q2 avg ≈ 1 probe); duplicate tool+args fingerprints refused; chat at **temperature 0** for stabler JSON.  
**Why:** Agency only where the next hop depends on prior evidence *and* follow left a gap.  
**Trade-off:** Less autonomous than free ReAct; drawable, budgetable, checkpointable.  
**Why:** *“Why LangGraph instead of a while-loop / free ReAct?”* — Explicit nodes/edges are a state machine you can draw, budget, and STM-phase. A while-loop works until you must explain cost and failure clearly.  
**Why:** *“Why temperature 0?”* — Delivery status is not creative writing; stable tool JSON and calmer demos.

### Budgets: cost control vs finishing the job

Unlimited loops burn money. Blind caps can stop one hop short of the proving email. Synapse **shapes the loop** so must-have work is cheap and fixed; the uncertain part is small; a stop still returns an honest partial.

```text
Cheap + must-have     CODE: gather → follow     (predictable tool count)
Expensive + optional  LLM:  probe*              (max_probe_steps)
Always before answer  CODE: finish → evidence → write answer
Hard ceiling          max_tool_calls (default 28)
```

| Lever | Effect |
|---|---|
| Front-load gather + follow | Cap hits optional probe first, not core live evidence |
| Checklist = “done” | Stop when slots filled **or** budget spent — not when the model “feels” done |
| finish after probe | One last repeatable fill even if probe caps out |
| gaps / conflicts in JSON | Honest partial > fake complete or infinite retry |
| Fingerprints | Same tool+args twice refused — stops cost burn |

**Why:** *“What if the cap hits, and a higher cap would have finished the job?”* — That can happen on a **leftover probe hop**. It should not drop the live risks: those hops already ran in code. After the cap, **finish** still fills empty slots once. The model then writes from the pack it has. Anything still missing is a **gap** (“no meetings found”), not a fake complete answer. Raising the cap might fetch one more email. It also might not. Open search has no flag that says “the objective is achieved.”  
**Why:** *“Why not raise the cap until it succeeds?”* — There is no clean success test for open retrieval. A higher cap is more spend, not a proof. We cut probe steps from 6 to 3 because the measured ATLAS Q2 ask used about **1** probe step (`docs/perf_results.json`). Quality comes from better first hops, not a bigger loop.  
**Why:** *“How do you know you’re done?”* — The checklist is filled or partial, then the graph exits and writes from the pack. Done is leaving the state machine. It is not the model feeling finished.  
**Why:** *“Why return gaps instead of retrying forever?”* — A gap tells the user what was not found. Retrying forever spends money and looks like quality.  
**Common mistake:** “Runs until the objective is achieved” with no coded stop. That is an open bill. A cap with no front-loaded gather is a coin-flip demo.

These are different stops. Do not mix them up in an interview:

| Stop | What tripped | What the caller gets |
|---|---|---|
| Probe cap (3) | Optional model-chosen hops | Answer from the pack + gaps. Finish already ran. |
| Tool cap (28) | Too many tool calls in one ask | Run fails (`tool_budget_exceeded`). Core hops are in front so this is rare. |
| Ask deadline (120s) | Wall clock | **503** `ask_deadline_exceeded`. No made-up answer. |
| Admission (4 asks, 2 per tenant) | Too many asks at once | **429**. The agent does not start. |

**Finish — What:** Code fills remaining empty slots best-effort; skips what follow already covered.  
**Evidence / write answer — What:** Assemble pack with trust order; model must return JSON; **`parse_synthesis`** (Pydantic) validates or falls back; **`ground_citations`** drops ids not in the pack; soft output flags (secret-shaped text) become gaps. Plan/probe use the same pattern — **`parse_plan` / `parse_probe`** allowlist slots and tools so unknown probe tools never run.  
**Why we can trust the answer (same breath as write answer):** trust is not “the model is smart” — it is what code lets leave the building (input policy + allowlisted tools + structured parse + citation checks + which-source-wins order + short-term run log).

```text
Model proposes answer + citation ids
    → ground_citations(pack) keeps only real ids
    → live ≻ external ≻ RAG ≻ LTM already in the pack prompt
    → response: answer, plan, checklist, hops, evidence summary,
                 citations, rejected_citations, gaps, conflicts, guardrail, tool_trail
    → STM checkpoints the run
    → UI Execution: plan → [LIVE|RAG|LTM|EXTERNAL] hops → evidence → guards → answer
```

| Mechanism | Skeptic hears |
|---|---|
| Citations must already be in the pack | Invented risk ids die |
| EvidencePack-only prose | Writer stays inside retrieved evidence (externals framed as DATA) |
| Live wins | Green PDF cannot silently beat `at_risk` |
| hops + tool_trail + STM | Auditable why a hop ran |
| gaps | Thin evidence visible, not papered over |
| UI Execution view | Product story visible without dumping private CoT |

**Why:** *“How do you trust an LLM answer?”* — I don’t. I trust tools + pack + grounding + audit trail. The model is a writer in a cage.  
**Why:** *“What if it ignores the pack?”* — Citations fail grounding; gaps/conflicts expose thin evidence. Golden eval (Chunk 14) regresses pack ids + live status without an LLM judge.  
**Why:** *“Why not multi-agent?”* — One user question, one objective. Extra agents need a second goal or handoff; else coordination is free complexity.

---

## 3. MCP / tools → authorized data → agent

```text
Agent (or MCP host)
    │  tool name + args  (NO tenant_id)
    ▼
ToolSession(logged-in user from JWT / MCP env)
    │
    ├─ LIVE DB connectors (SQL at ask time)
    │     project_lookup / risk_list / blocker_list / task_search / project_activity
    │     email_search / meeting_search
    │        ──▶ Postgres WHERE tenant_id = logged-in tenant id
    │
    ├─ RAG connector
    │     document_search ──▶ embed query → existing pgvector chunks (no reindex on ask)
    │
    ├─ PUBLIC EXTERNAL connectors (fetched at ask time, not stored)
    │     hn_search              ──▶ Hacker News Algolia (no key)
    │     stackoverflow_search   ──▶ Stack Exchange API (key optional)
    │     tavily_search          ──▶ Tavily web search (SYNAPSE_TAVILY_API_KEY)
    │
    └─ memory_search / memory_write ──▶ memory_entries (tenant-scoped)
    │
    ▼
typed result → EvidencePack item (framed as DATA if text)
```

**What:** Tools are the only way the model touches systems — that *is* the connector layer.  
**Why:** Capability-scoped functions beat “here is a SQL string.”  
**Live DB connectors:** Not a separate missing product. `project_lookup`, `risk_list`, … **are** live data access: every ask hits current Postgres rows.  
**Public externals:** Same tool contract; `provider` + `mode` in the payload. A missing Tavily key is a gap.  
**Ops:** `GET /v1/connectors` → `live_db` + `public_external`; `GET /v1/connectors/ready` probes HN and Stack Overflow, and Tavily when a key is set.  
**Example:** `risk_list({project_key:"ATLAS"})` — tenant from the logged-in user, not args.  
**Fails:** Bad args → tool error; network blip → gap; missing Tavily key → `tavily_search:unavailable`.  
**Why:** *“Why MCP instead of direct DB access?”* — MCP is an **interface**. Security is logged-in user binding + SQL filters.  
**Why:** *“Why tools never take `tenant_id`?”* — Anything the model can type can be spoofed. Tenant comes from the logged-in user from JWT bound into `ToolSession`.  
**Why:** *“Where does the public web come from?”* — Hacker News, Stack Overflow, and Tavily, called when the hop runs. Those pages are not a dataset in the repo.

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
                               EvidencePack (trust rank 0 (highest))

                    ┌─────────────────────────────────────────┐
  Document body     │  documents table                        │
  changes / seed    │         │                               │
                    │         ▼  synapse-ingest (offline/CLI) │
                    │  chunk → embed → document_chunks        │
                    │  (pgvector); content_hash per chunk     │
                    └─────────────────────────────────────────┘
                                      │
                                      │  document_search at ASK time
                                      │  = embed(query) + similar-chunk search only
                                      │  ≠ re-ingest documents
                                      ▼
                               EvidencePack (trust rank 10)
```

### What happens on each ask

| Event | Live DB | RAG vectors |
|---|---|---|
| `POST /v1/ask` | **SELECT** current rows via tools | **Retrieve** existing chunks; embed the *query* only |
| `PATCH` project status | Row updates immediately | **Untouched** — docs may now conflict (detected in pack) |
| New risk / blocker / email | Inserted; next ask sees it | **Untouched** |
| New/edited document | Row in `documents` | Run **`synapse-ingest`** to re-chunk/re-embed **that** document pass |
| Every ask | — | **Do not** wipe/rebuild the vector index |

### Requirement → Problem → Primitive → Choice → Trade-off

| | |
|---|---|
| **Requirement** | Answers must reflect *today’s* delivery state and still use long prose docs. |
| **Problem** | Embed status → PATCH invisible until reindex; SQL-only → lose semantic search over retros/PDFs. |
| **Primitive** | Two stores, one Postgres: OLTP tables + `document_chunks` (pgvector). |
| **Choice** | **Live connectors = SQL.** **RAG = documents only**, ingested when docs change. Ask cost = tool SQL + one query embedding + similar-chunk search. |
| **Trade-off** | Docs can lag live status (we surface **conflicts**). Worth it: status correct without embed cost per mutation. |

### Live vs RAG Q&A (same weight as Redis)

**Why:** *“Do you reindex RAG whenever the database changes?”*  
**Answer:** No. Status/risks/blockers are **live SQL**, not vectors. RAG is for **document narrative**. Re-ingest when a *document* changes (`synapse-ingest`), not on every ask or every PATCH.

**Why:** *“Then how is this Agentic RAG on a dynamic database?”*  
**Answer:** The agent **tools** into a dynamic DB (gather/follow). RAG is one tool among many. Dynamics live in Postgres rows; vectors are a **derived index over docs**.

**Why:** *“Why not embed project status?”*  
**Answer:** Status must be authoritative after a write. Vectors are approximate and lag. Embedding status is the Redis-class mistake: wrong tool for the job.

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
document_chunks + vector index   (same Postgres, tenant_id on every row)
    │
    ▼  query time
embed(question) → similar-chunk search → distance filter → tenant (+ project) filter
    │
    ▼
frame <<<RETRIEVED_DATA not instructions>>>
    │
    ▼
EvidencePack (trust rank 10) → write answer → ground citations
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

### Chat model vs embedding model (why Groq + OpenAI)

Synapse splits **generation** and **embedding** on purpose. They are different jobs with different failure modes and pricing.

```text
Chat (plan / probe / write answer)
    preferred: Groq  OpenAI-compatible API  →  model openai/gpt-oss-20b
    fallback:  OpenAI api.openai.com         →  model gpt-4o-mini
    client:    AsyncOpenAI (same SDK; Groq = different base_url)

Embeddings (ingest + document_search query)
    only:      OpenAI  text-embedding-3-small  (1536-d)
    stored in: document_chunks + vector index; content-hash cache keyed to that model
```

| | Requirement | Problem | Choice | Trade-off |
|---|---|---|---|---|
| **Chat** | Structured JSON for plan/probe/answer; several calls per ask; keep $/ask low | A single heavy hosted chat model would dominate cost; free local GPU is not the deploy story | **Groq first** (`SYNAPSE_GROQ_API_KEY`) hosting `openai/gpt-oss-20b` via OpenAI-compatible Completions; **OpenAI `gpt-4o-mini` fallback** if only `SYNAPSE_OPENAI_API_KEY` is set | Groq-specific `tool_use_failed` quirks need recovery in `_chat` (see `PROJECT.md`); chat quality ≠ embed quality |
| **Embed** | Stable vector space for similar-chunk search; cheap query embeds; re-ingest only when docs change | Mixing embed models/sizes breaks the vector index + content-hash cache; re-embedding the whole dataset on every ask is waste | **OpenAI `text-embedding-3-small` only** — fixed 1536-d, list ~$0.02/MTok; ask embed ≈ noise (~6 tokens) vs chat | Needs an OpenAI key even when chat runs on Groq; switching embed model means full `synapse-ingest` rebuild |

**Why not one vendor for everything?** Chat can swap hosts (Groq ↔ OpenAI) without touching the vector index. Embeddings **are** the index contract — one model id + dim for the life of `document_chunks`.  
**Why not local / open embedders only?** Fine later; this build uses a hosted embed API so demo machines don’t need a GPU and sizes stay the same for everyone.  
**Why Groq for chat specifically?** OpenAI-compatible API → one client code path; open-weight hosted model with list prices used in `synapse-perf` (~$0.10/$0.50 per MTok in/out) → measured ask ≈ **$0.00095** under the ATLAS Q2 test run.  
**Why OpenAI embeddings specifically?** Reliable hosted embed product Synapse already wires through `Embedder`; cheap enough that embed cost is not the ask bill; matches pgvector rows tagged `embedding_model` / `embedding_dim`.

---

## 6. Live vs external vs RAG vs STM vs LTM

```text
Highest trust                         Lowest trust
─────────────────────────────────────────────────────
LIVE Postgres rows
   │  (project status, risks, blockers, tasks, activity, email, meetings)
EXTERNAL APIs
   │  (Hacker News, Stack Overflow, Tavily) — live web, not our ledger
RAG chunks
   │  (documents; may be stale)
LTM memory_entries
   │  (prior summaries/facts; TTL + supersede)
```

**Live — Why top:** PATCH status must be believed immediately.  
**External — Why below live:** A public post is a signal, not the project row.  
**RAG — Why below both:** Docs lag.  
**LTM — Why bottom:** Helpful recall; **never** overrides live status.  
**STM — Different axis:** Not “knowledge,” but **this run’s** checkpoints (execution state).

**Why:** *“Why STM and LTM instead of saving the chat?”* — Chat logs mix tool noise with facts; STM is auditable phases; LTM is curated durable notes with lifecycle.

**Example (Atlas):** Live `at_risk` plus the open SDK blocker beat the March status report that still says the June cutover is on track. The answer names that conflict.

---

## 7. Login + permissions + tenant isolation

```text
Login → bcrypt verify → JWT
Request → Bearer required
       → tid from token
       → SQL always AND tenant_id = tid
       → project membership / role for writes
```

**Why both login and permissions:** Knowing *who* ≠ knowing *what they may do*.  
**Why filter in the data-access layer:** UI checks are skippable; SQL is not.  
**Why:** *“Where does identity come from?”* — Token / logged-in user. Never from the model.

---

## 8. Failure behaviour & reliability (as it works today)

```text
External GET / LLM / embed
    │
    ├─ parent ask deadline (default 120s) cancels the graph
    │     chat timeout 25s · embed timeout 20s · tool HTTP 12–25s
    │     UI client waits 180s, so the server fails first
    ├─ retry with exponential backoff + jitter
    │     only on 408/429/5xx and transport blips
    │     NOT on 4xx (except 408/429) — fail-fast auth/validation
    ├─ chat circuit: 5 consecutive transient chat failures → open 30s → one probe
    ├─ admission: 4 in-flight asks, 2 per tenant → 429 Retry-After (in-process)
    ├─ Idempotency-Key on POST /v1/ask → replay completed, 409 if still running
    ├─ duplicate tool fingerprint → refuse (safe-to-retry gather/follow/probe)
    ├─ startup reaper: leftover status=running → failed (orphaned_on_restart)
    ├─ missing personal token → public fallback (still live)
    ├─ empty retrieval → gap (continue)
    └─ hard exception → STM.fail with a stable code (not the provider body)
                      → GET /v1/runs/failed → POST …/acknowledge after review
```

**Retries — Why only some errors:** Retrying a 401 burns quota and hides bugs. Retrying a 503 often succeeds.  
**Idempotency — Why fingerprints and why a key:** Same tool+args twice in one run is a loop; refuse it. A client that times out and retries `POST /v1/ask` is a second full agent unless the caller sends `Idempotency-Key`. The key is stored on `agent_runs` (tenant + user + key). Completed → replay the saved response, no second model call. Still running → **409**. Failed → one compare-and-set reclaim, then execute again. A different question with the same key → **422**. This is not an answer cache: a new key (or no key) always reads live rows.  
**Admission — Why a semaphore, not Redis:** One API process accepts asks. A process semaphore is the matching primitive. Redis stays a health ping. At more than one API process the same numbers belong in Redis and this in-process gate must go — it does not coordinate across processes.  
**Deadline — Why nested timeouts:** Measured ask p95 on this machine is 45.3 s (`docs/perf_results.json`, n=3). The server deadline is 120 s so a normal ask finishes and a hung provider cannot run for the SDK default after the UI has stopped waiting at 180 s. `wait_for` cancels the graph; chat and embed clients have their own shorter timeouts.  
**Circuit — Why chat only:** Chat is on every ask. A dead provider would otherwise be retried 3 times per call, on every concurrent ask. Five consecutive transient chat failures open the circuit for 30 s; asks then fail with `llm_unavailable` and do not invent an answer. A down HN, Stack Overflow, or Tavily call stays a gap — that failure must not stop live SQL.  
**Orphan runs — Why a startup reaper:** Ask is synchronous and in-process. If the process dies after `agent_runs.status=running` and before `fail`, nothing is left to finish the row. On the next start of this single process, every `running` row is marked `orphaned_on_restart` and shows up on the failed-ask list. Do not run that reaper in every replica: it would kill asks another process still owns.  
**Pool — Why the size is explicit:** This process uses pool 5 + overflow 3, and refuses a config where `pool + overflow < ask_max_inflight + 1`. One replica’s ceiling is 8 connections. Twenty replicas would be 160 — shrink the pool as you add processes, or the database is what falls over.  
**failed-ask list — Why not SQS yet:** Ask is still **synchronous**. Failed runs in Postgres *are* the failed-run list until we add accept→worker. Claiming SQS without a queue would be dishonest.  
**Why:** *“Why timeout here?”* — One hung provider call must not outlive the ask, and the ask must not outlive the client.  
**Why:** *“Retry vs fail-fast?”* — Retry transient safe-to-retry reads; fail-fast on auth, bad args, and an open chat circuit.  
**Why:** *“Where is the failed-job queue?”* — `GET /v1/runs/failed` today; a real SQS queue only when async ask lands.  
**Why:** *“What if the client retries?”* — Send the same `Idempotency-Key`. You get the first result, or 409 while it is still running.  
**Why:** *“What if traffic exceeds capacity?”* — The fifth concurrent ask (or the third from one tenant) gets **429**. It does not queue.

---

## 9. Evaluation (golden, as it works today)

```text
synapse-eval (or pytest)
    │
    ▼
seed 42 / profile ci  →  ToolSession(logged-in user from JWT)
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
SuiteReport: pass rate = cases_passed / n   (only the measured overall rate)
```

**Requirement:** Prove ATLAS Q2 (and HARBOR delayed) still retrieve the right live ids after refactors.  
**Problem:** LLM-as-judge invents scores and drifts; you cannot rely on a floating “0.87 quality” score.  
**Building block:** Closed-world checks against seed-stable ids (`rsk_nw_00003`, `eml_nw_00007`, …).  
**Choice:** Gate on **multihop pack** first (repeatable). Answer scoring is fixture/ask-optional — same checks, still no judge model.  
**Trade-off:** Does not score prose elegance; that is intentional. Adversarial input is Chunk 15 (`synapse-redteam`), not retrieval regression.

**Why:** *“What’s your eval score?”* — Suite **pass rate** on golden cases (today 2/2 when DB seeded). Not BLEU, not GPT-judge.  
**Why:** *“Why not judge with another LLM?”* — Circular and non-reproducible. Seed ids either land in the pack or they don’t.

---

## 10. Red team / injection (as it works today)

```text
Attack list (jailbreak, exfil, cross-tenant, tool-abuse, RAG inject, fake cites)
    │
    ├─ PyRIT converters (repeatable leet, base64, char-space, flip)
    │     + local fallbacks if pyrit extra missing
    ▼
score_policy_block  →  check_user_question MUST deny (defense win)
score_framing        →  injected doc/comment wrapped as <<<RETRIEVED_DATA>>>
score_grounding      →  invented / foreign ids rejected by ground_citations
benign controls      →  ATLAS/HARBOR asks still ALLOWED
HTTP (integration)   →  POST /v1/ask jailbreak → 400; foreign JWT+ATLAS → 404/403
    │
    ▼
synapse-redteam → RedTeamReport.pass rate   (only the measured overall rate)
```

**Requirement:** Prove the cage holds under *hard* injection, not just the happy path.  
**Problem:** A regex that only matches “ignore previous instructions” loses to Base64/leet/spacing; an LLM judge invents “safe.”  
**Building block:** PyRIT **converters** mutate seeds; Synapse scorers are repeatable (block / frame / reject).  
**Choice:** Gate CI on defense hold-rate — not on “model refused nicely.” Planted ATLAS injection document + comment exist in seed-42 for framing tests.  
**Trade-off:** We use PyRIT converters (optional extra `.[redteam]`), not full multi-turn Crescendo against live Groq in CI — that would be flaky/costly. Live ask adversarial runs are the next hardening loop, not a fake score.

**Why:** *“Did you red-team or just unit-test regex?”* — Both: PyRIT mutations + HTTP ask block + framing + grounding + cross-tenant 404.  
**Why:** *“What if injection is in a retrieved doc?”* — User channel ≠ data channel. Docs are DATA-framed; write answer + grounding still can’t mint foreign ids.

---

## 11. Observability (as it works today)

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

**Requirement:** Reconstruct one ask from logs and know overall health without a SaaS bill.  
**Problem:** LangSmith (or full OTel→Jaeger) is tempting next to LangGraph, but Synapse already persists run truth in STM + tool_trail. A second vendor trail duplicates cost and drifts from what `/v1/runs` shows.  
**Building block:** Correlation ID + structured spans + in-process counters/summaries.  
**Choice:** **No LangSmith.** Ship `GET /v1/metrics` and span logs. Prometheus scrape / OTel export is the honest upgrade for multi-instance later.  
**Trade-off:** Metrics reset on process restart; fine for local/demo, not a multi-node TSDB.

**Why:** *“Where’s LangSmith?”* — Deliberately skipped. STM checkpoints are the agent audit; metrics cover overall counts; LangSmith would be a parallel story that can disagree with Postgres.  
**Why:** *“How do you debug a bad answer?”* — `correlation_id` → logs → `run_id` → STM checkpoints + hops + rejected_citations.

---

## 12. Performance & cost (as it works today)

```text
synapse-perf ──▶ live /v1/ask + /v1/multihop
                    │
                    ├─ wall latency  → p50 / p95 / mean (summarize_latencies)
                    ├─ chat tokens   → UsageAccumulator (plan / probe / write answer)
                    ├─ embed tokens  → Embedder (content-hash cache hits counted)
                    └─ cost_usd      → list-price × measured tokens (estimate, not invoice)
                              │
                              ▼
                    docs/perf_results.json   + per-ask usage in /v1/ask response
```

**Requirement:** Know what an ask costs and how long it takes — without inventing an SLA.  
**Problem:** Budgets without measurements are superstition; premature “optimizations” (skip probe, cache answers) fight live-first correctness.  
**Building block:** Token accumulator on every chat/embed call + HTTP test runner against the same API the UI uses.  
**Choice:** Measure first (`synapse-perf`). Tighten `max_probe_steps` 6→3 because measured ATLAS Q2 averaged **1** probe step — not because theory said so. Did **not** skip probe entirely: residual agency still earns its one call. Did **not** cache ask answers: live PATCH demos would lie.  
**Trade-off:** Numbers are local wall-clock + list-price estimates. They support design choices; they are not a published multi-region SLA.

### Measured (local, seed-42 ATLAS Q2 — `docs/perf_results.json`)

| Path | n | p50 | p95 | avg chat calls | avg probe | avg tokens | est USD/ask |
|---|---|---|---|---|---|---|---|
| `/v1/ask` | 3 | **23.6 s** | **45.3 s** | **3.0** | **1.0** | **3866** | **~$0.00095** |
| `/v1/multihop` | 5 | **8.7 s** | **9.4 s** | 0 | — | 0 chat | ~$0 |

Pricing assumptions (see report): Groq `openai/gpt-oss-20b` ~$0.10/$0.50 per MTok in/out; `text-embedding-3-small` ~$0.02 per MTok. Embed spend on ask is noise (~6 tokens/query).

**Why:** *“What’s your p50?”* — ~24 s for full ask on this machine/dataset; ~9 s for LLM-free multihop. Not an SLA.  
**Why:** *“How did you cut cost?”* — Front-loaded gather/follow so probe averages one step; then lowered the cap to 3. Token accounting proved the graph was already cheap before we touched it.  
**Why:** *“Why not cache the answer?”* — Live status can PATCH between asks; answer cache would demo a lie.

---

## 13. Runtime topology (Compose)

```text
LOCAL
    Docker Compose (Postgres + Redis)
        → synapse-api :8000
        → synapse-ui :8501
        → POST /v1/ask/stream

AWS (one instance, no autoscale)
    terraform apply
        → EC2 runs the same Compose file plus docker-compose.aws.yml
        → one API process, UI, Postgres, Redis
        → security group opens 8000 and 8501 only
        → JWT and API keys from Secrets Manager
```

**What actually runs locally:** Docker Compose starts Postgres and Redis. You run the API and the UI on the host. That is one API process, which is what the admission cap and the orphan reaper assume.

**What Terraform creates:** one VPC, one public subnet, one security group, one EC2 instance, one instance role, and an empty Secrets Manager secret. The instance clones the repo, reads the secret into `.env`, and starts Compose. There is no load balancer, no second task, and no autoscaling. A second API process would make the in-process 429 and the startup reaper wrong.

**Why this shape:** The application is one process. The cloud layout copies that. It does not add a queue or a cluster.

**Failure:** If the secret is empty, bootstrap writes nothing useful and the API does not become healthy. Logs are `/var/log/synapse-bootstrap.log` on the box. Read them with SSM. There is no SSH key.

**Trade-off:** `terraform apply` was run. The instance cloned GitHub and stopped because `docker-compose.aws.yml` was not on `main` yet, so the API never became healthy. `terraform destroy` then removed the instance, network, role, and secret. Nothing from that apply is still running. Do not describe a live AWS environment.

---

## 14. CI gates (product path)

```text
PR / push main
    │
    ├─ lint + mypy
    ├─ pytest (unit + integration, pgvector Postgres + Redis)
    ├─ synapse-eval          ← golden pack ids / live status (no LLM judge)
    ├─ synapse-redteam       ← PyRIT converters + policy/framing/grounding
    ├─ pip-audit             ← dependency vulns (skip editable self)
    └─ docker build
         │
         ▼
      job ci-ok (needs all)
```

**Requirement:** Broken retrieval or tenancy defense must fail the merge.  
**Choice:** Eval and red-team are **required jobs** (same CLIs as local).  
**Why:** *“What’s your release gate?”* — `ci-ok`: lint, tests, golden eval, red-team, audit, image build, e2e.  
**What CI does not do:** It does not run `terraform apply`. It does run `terraform validate`. There is no deploy workflow. Shipping to AWS is the manual Terraform flow in the README.

---

## 15. End-to-end & failure proof

```text
pytest -m e2e  (CI)
    TestClient + seeded Postgres
    ├─ happy: auth → PATCH live status → multihop reflects it → metrics
    └─ fail:  jailbreak 400 · cross-tenant 404 · bad JWT 401
              · failed-ask fail+ack · tool budget RuntimeError · Redis-down health 503

synapse-e2e
    live HTTP → docs/e2e_results.json
    same spine + optional /v1/ask when chat key present
```

**Measured:** ASGI **5/5**; live **9/9** (`docs/e2e_results.json`).

**Why:** *“What did you break on purpose?”* — Redis ping, tool budget, jailbreak, other-company access, bad JWT, injected run failure → failed-ask list.

---

## 16. Skipped on purpose — do not claim as shipping

| Item | Why skipped |
|---|---|
| Second API process, ECS service, EKS, autoscale | Admission and the orphan reaper are in-process. Terraform is one EC2. |
| Automated CD | GitHub Actions is CI. It does not apply Terraform. |
| SQS + async ask worker | Sync ask + Postgres failed-ask list covers the path |
| Redis rate limit / semantic cache | One API process uses an in-process semaphore; Redis is health only. Answer cache would disagree with live PATCH |
| Chat circuit on HN/SO/Tavily | Those failures become gaps. The circuit is on chat, which every ask needs |
| Multi-replica orphan reaper | Startup reaper assumes one API process |
| LangSmith / full OTel→Jaeger | STM checkpoints already audit the agent run |
| Answer / semantic result cache | Would disagree with live PATCH demos |
| Extra microservices / multi-agent | One FastAPI process, one objective |
| GitHub issues, Wikipedia, Slack, Gmail connectors | Removed. Live web is Hacker News, Stack Overflow, and Tavily |
| Published multi-region latency SLA | Local p50 in `docs/perf_results.json` only |

---

## 17. Design checklist

1. JWT tenant → SQL filter → tools without tenant args.  
2. Input guardrail before spend; structured parse on plan/probe/answer.  
3. LangGraph: gather → follow (code) → probe (capped LLM) → finish → ground.  
4. Caps vs quality *inside* that graph: front-load must-have hops; probe capped; finish + gaps.  
5. Trust = pack + citation checks + which-source-wins order + short-term run log.  
6. Live SQL ≠ RAG; do not reindex vectors on every ask or PATCH.  
7. Trust order: live ≻ external ≻ RAG ≻ LTM.  
8. STM = run audit; LTM = durable notes; Redis ≠ truth.  
9. Sync ask + failed-ask list (`/v1/runs/failed`).  
10. UI Execution view streams real node events (plan, tools, hops, evidence, citations) then the answer. Not chain-of-thought.  
11. Golden eval = seed ids + live status; pass rate only — no LLM judge.  
12. Red team = PyRIT mutations blocked/framed/ungrounded.  
13. Obs = correlation_id + spans + `/v1/metrics`.  
14. Measure before optimize — `synapse-perf` tokens/latency.  
15. Merge gate = `ci-ok` (eval + redteam + audit + image + e2e).  
16. Chat = Groq `openai/gpt-oss-20b` (OpenAI fallback); embeds = `text-embedding-3-small` only.  
17. Docs: README runs/demos · ARCHITECTURE design · PROJECT narrates — no invented metrics.  
18. Ask admission is in-process (4 global / 2 per tenant) → 429. Not a Redis limiter until there is more than one API process.  
19. Ask deadline 120s > chat 25s; UI client 180s. Chat circuit opens after 5 transient failures.  
20. `Idempotency-Key` replays a completed ask. Startup reaper fails leftover `running` rows. DB pool is sized to inflight asks.  
