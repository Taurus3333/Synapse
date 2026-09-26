# Pitch — walk me through Synapse

I built Synapse to answer one decision: **can Northwind Logistics still make the Atlas platform cutover before the 30 June code freeze?**

Harbor Identity owns the vendor SDK Atlas is waiting on. That SDK missed the May drop. A steering meeting needs one brief. The facts are split. Today's status, the open risk, and the blocker sit in a **live database** and change when someone updates a row. The March **status report** still says the June cutover is on track. The May **email** and the June **retro** explain the miss. A **note** from an earlier ask is useful and must not override today's row. The vendor's own public discussion is **not in Northwind's database**, so the brief also looks at Hacker News, Stack Overflow, and Tavily.

A chatbot guesses. Simple RAG returns the stale report and misses today's status. The next document is not known until the live risk is read, so the path has to be multi-hop.

The question is: can the Atlas cutover still make the freeze, what changed in Q2, what is the vendor SDK blocker, and does the status report agree with live status?

You log in as Northwind, pick Atlas, and ask that. The system plans evidence slots, loads live Postgres first, then code follows the email, the retro, the documents, and the public web from the open SDK risk. It may probe once if something is still missing. It builds an evidence pack, writes an answer, and keeps only citation IDs that were actually fetched. In the UI you see **Execution**: the plan, hops tagged LIVE / RAG / LTM / EXTERNAL, evidence counts, guardrail/citation check, then the answer.

I did not give the model an open tool loop. Code owns the first hops. The model fills leftovers under a hard budget. Live status outranks the March report, public pages, and long-term notes. Short-term memory is the run log. Long-term notes are soft recall. I did **not** add an answer cache — Demo Lab can PATCH status, and a cache would lie.

If someone asks “what if the cap stops you one hop before the answer?”, I say this: the live risks were already loaded. Finish still runs. The answer is written from that evidence, and missing pieces are gaps. I do not raise the cap until it “succeeds.” Open search has no success flag, and the measured Atlas ask only needed about one probe step. A higher cap is more spend. It is not proof.

The tools are the cutover record, exposed over MCP: project, tasks, risks, blockers, activity, documents, email, meetings, the three public sources, and memory. Arguments never include a tenant id. Each call has a timeout. A missing Tavily key is a gap, not a crash.

Project 5 is stricter checks on that same `/v1/ask` path: jailbreak blocks, citations checked in code, Globex asking for Atlas gets not-found, PyRIT and seed eval in GitHub Actions. Locally I run Postgres and Redis in Docker and the API and UI beside them. The UI reads `POST /v1/ask/stream` and shows each graph step as it finishes: plan, tools, hops, evidence counts, citation check, then the answer. Those events are the node names. They are not the model's private draft.

GitHub Actions is CI. It does not deploy. Terraform in `infra/terraform` is one EC2, one security group, and a Secrets Manager secret. The instance is meant to run the same Compose stack with one API process, because the admission cap and the crash reaper assume that. I did run `terraform apply`. The box cloned the repo and stopped, because `docker-compose.aws.yml` was not on main yet, so the API never came up. I then ran `terraform destroy`. That removed the instance, the network, the role, and the secret. Nothing from that apply is still running.

The sync ask is also capped in a different way: four asks at once, two per tenant, a 120 second deadline, a chat circuit breaker, and an idempotency key so a client retry does not run the agent twice. A crash leaves `running` rows; the next start marks them failed. Redis is a health check. It is not a cache and not the rate limiter.

Evidence from real runs: retrieval eval **2/2**, UI demos **13/13**, live e2e **9/9**, red-team **pass rate 1.0**, local ask about **~$0.001** and p50 around **24 s**. Those runs used the shorter Q2 wording of this same cutover. They are observations, not an SLA, and they were not re-measured after the question text was tightened.

Gaps: sync ask only; company mail is seeded Postgres, not a live inbox; guardrails are a strong first line, not perfect safety.

What I learned: pick the right store for each fact in the brief, put hard limits around the model, prove the cutover ids with pass/fail checks, and keep infrastructure from becoming the product.
