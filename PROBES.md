# Synapse — Probes (matches RESUME.md)

Defensible answers only. Metrics from `docs/*.json` / CLIs.

---

### Bullet

Built the brief a Northwind delivery lead takes to steering: can Atlas still make the 30 June freeze while Harbor's vendor SDK is late. Reads **live Postgres first**, then the status report, mail, and meetings, then durable notes — with grounded citations and a UI **Execution** view (plan → lane-tagged hops → evidence → guards → answer).

### Likely probe

What problem does this solve that simple RAG does not?

### Answer

Status changes in SQL. The March status report still says the June cutover is on track. Notes help but must not override live truth. Synapse reads the Atlas and Harbor rows first, then the report, mail, and meetings, then LTM. While the ask runs, the UI shows events from `POST /v1/ask/stream` (plan, tool names, hop names, evidence counts, citation counts). The final bubble still has the Execution sections. Neither view prints the model draft.

### Cross questions

- What is the trust order on the cutover brief?  
- What does the UI show for one Atlas freeze ask?  
- Seeded company mail vs a live inbox — what do you claim?

### Remember

- Product = live + RAG + memory + grounded answer.

---

### Bullet

Bounded multi-hop: code loads the open SDK risk first and follows email, retro, documents, and public web (Hacker News, Stack Overflow, Tavily). The model may fill leftovers under a probe cap. If the cap hits, finish still runs and the answer lists gaps. Tools are MCP-wrapped. The model never gets raw SQL.

### Likely probe

Is this really agentic or just a LangGraph script?

### Answer

Outer flow is fixed (plan→gather→follow→probe*→evidence→answer). Agency is residual: the model may pick one allowlisted tool while slots are empty and budget remains. First hops from live risks are code (`plan_follow_hops`). That is intentional — budgetable and auditable.

### Cross questions

- Who picks the next hop after the open vendor SDK risk?  
- How do you stop runaway tool loops?  
- What do hops look like in the response payload?

### Follow-up (say this if they push)

**“The cap stopped you. A higher cap would have found the email. So the cap made you wrong.”**

Separate the stops. They are not the same knob.

| Stop | Default | Caller sees |
|---|---|---|
| Probe steps | 3 | Answer from the pack, plus gaps. Finish already ran. |
| Tool calls | 28 | `tool_budget_exceeded`. Live hops run first, so this is the backstop. |
| Ask deadline | 120s | **503**. No invented answer. UI waits 180s. |
| Asks in flight | 4 total, 2 per tenant | **429**. This ask never starts. |

What I say:

The miss, if it happens, is a leftover probe hop. Live risks, blockers, and the code-owned follow hops already ran. After the probe cap, finish still tries empty slots once. Then we write from the pack. If the proving email was never fetched, the answer says that as a gap. I do not loop until the model feels done.

Why I do not “just raise the cap”: open search has no done flag. A bigger cap can still miss, and it always costs more. We cut probe from 6 to 3 because the measured ATLAS Q2 ask averaged about **1** probe step (`docs/perf_results.json`, n=3). That number is a local observation, not a promise that one step is always enough.

Same tool and same args twice is refused (fingerprint). That stops a loop. It is not the probe cap.

### Remember

- Workflow outside; capped agent inside.
- Cap hit → partial answer + gaps. Not a silent success. Not a 429.

---

### Bullet

Separated **STM** (per-ask run log) from **LTM** (durable notes); live status always outranks memory; no answer cache so a status PATCH cannot be lied about.

### Likely probe

Why no Mem0 / answer cache?

### Answer

STM = execution audit in Postgres. LTM = soft notes, lowest trust. Mem0 would add another memory story that can fight live SQL. Answer cache would disagree after Demo Lab status PATCH. Embed content-hash cache only.

### Cross questions

- What happens on status PATCH?  
- Where are STM checkpoints stored?

### Remember

- Right store for the job.

---

### Bullet

Added Project 5 controls on the same path: jailbreak/exfil blocks, citation grounding, tenant isolation (cross-tenant **404**), PyRIT red-team **pass rate 1.0**, retrieval eval **2/2**, CI `ci-ok`.

### Likely probe

Is Project 5 a separate product?

### Answer

No. Same `/v1/ask` path with a harder cage. Eval checks seed pack IDs without an LLM judge. Red-team uses PyRIT converters; scorers are block/frame/ground — reported pass rate 1.0 from the last run.

### Cross questions

- Direct vs indirect injection?  
- Why 404 not 403?  
- Where is the number from?

### Remember

- Production layer on the product path.

---

### Bullet

Local proof on Docker Compose: UI demos **13/13**, live e2e **9/9**, about **$0.00095**/ask (list price × measured tokens). GitHub Actions CI runs lint, tests, eval, red-team, and the image build.

### Likely probe

Are those numbers an SLA? Where does this run?

### Answer

Numbers are local artifacts (`docs/*.json`), not an SLA. Locally, Docker Compose runs Postgres and Redis. The API and UI run on the host. GitHub Actions runs lint, tests, eval, red-team, audit, and the image build. It does not deploy. `infra/terraform` is one EC2, one security group (ports 8000 and 8501), SSM, and a Secrets Manager secret. The instance runs `docker-compose.yml` plus `docker-compose.aws.yml`, still one API process. `terraform apply` was run. The instance cloned the repo and stopped because `docker-compose.aws.yml` was not on main yet, so the API did not become healthy. `terraform destroy` then removed the instance, network, role, and secret. Do not claim a live AWS environment. Red-team was re-run: pass rate 1.0, 195/195, PyRIT available. Retrieval eval 2/2 was not re-run this pass.

### Cross questions

- What did you skip on purpose?
- How does Terraform provision this, and what does destroy remove?
- What is in the live trace for one Atlas freeze ask?

### Remember

- Product + system design; cloud deploy is not the product.

---

### Bullet

Bounded the sync ask itself: in-process concurrency cap (429), 120s deadline under the UI timeout, chat circuit breaker, `Idempotency-Key` replay, startup reaper for crashed runs. Redis stays health-only.

### Likely probe

What happens when traffic exceeds capacity? Why isn’t that Redis? What if the client retries? What if the process dies mid-ask?

### Answer

One API process. A semaphore admits 4 asks, 2 per tenant; the next gets 429 and does not queue. Redis is only a health ping — a counter there would not be shared until there is a second process, and it is the wrong store for run truth. `Idempotency-Key` on `agent_runs` replays a finished ask and returns 409 while that key is still running. It is not an answer cache: a new key reads live rows again. If the process dies, leftover `running` rows are marked `orphaned_on_restart` on the next start. That reaper is wrong if you run more than one API process. Chat failures open a circuit after 5 transient errors so a dead provider is not retried on every ask; the ask fails `llm_unavailable` instead of inventing an answer. The DB pool is 5 + 3 overflow for this one process — replicas multiply that.

### Cross questions

- Why not SQS?  
- Why not cache the answer?  
- What is the timeout chain?

### Remember

- Deadline: UI 180s > ask 120s > chat 25s. Measured p95 is 45.3s (n=3), not an SLA.
- Queue, Redis limiter, and multi-replica reaper are intentionally not implemented.
