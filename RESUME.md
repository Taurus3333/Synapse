# Synapse — Resume

## SKILLS

**Programming** · Python  

**Generative AI** · Agentic RAG, live-data retrieval, multi-hop orchestration, semantic search (pgvector), citation grounding, STM/LTM, prompt guardrails  

**Frameworks** · LangGraph, FastAPI, Streamlit, Pydantic, PyRIT  

**LLM Platforms** · Groq, OpenAI  

**Data & Memory** · PostgreSQL, pgvector, Redis (health only)  

**Engineering** · Docker Compose, Git, JWT, multi-tenant APIs, in-process admission, idempotent asks, GitHub Actions CI, Terraform (one EC2; apply and destroy were run; the API did not become healthy on that boot)  

---

## PROJECT

**Synapse** — Atlas platform cutover go/no-go  
*Project 4: Agentic RAG over the live cutover record + memory · Project 5: evaluated guardrail agent + CI*

- Built the brief a Northwind delivery lead takes to steering: can Atlas still make the 30 June freeze while Harbor's vendor SDK is late. Reads **live Postgres first**, then the status report, mail, and meetings, then durable notes — with grounded citations. The UI streams the real graph steps (`POST /v1/ask/stream`): plan, tools, hops, evidence counts, citation check, then the answer. Not chain-of-thought.
- Bounded multi-hop: code loads the open SDK risk first and follows email, retro, documents, and public web (Hacker News, Stack Overflow, Tavily). The model may fill leftovers under a probe cap. If the cap hits, finish still runs and the answer lists gaps. Tools are MCP-wrapped. The model never gets raw SQL.
- Separated **STM** (per-ask run log) from **LTM** (durable notes); live status always outranks memory and the stale March report; no answer cache so a status PATCH cannot be lied about.
- Added Project 5 controls on the same path: jailbreak/exfil blocks, citation grounding, tenant isolation (cross-tenant **404**), PyRIT red-team **pass rate 1.0**, retrieval eval **2/2**, CI `ci-ok`.
- Bounded the sync ask itself: in-process concurrency cap (429), 120s deadline under the UI timeout, chat circuit breaker, `Idempotency-Key` replay, startup reaper for crashed runs. Redis stays health-only.
- Local proof on Docker Compose: UI demos **13/13**, live e2e **9/9**, about **$0.00095**/ask (list price × measured tokens). GitHub Actions CI runs lint, tests, eval, red-team, and the image build. It does not deploy. Terraform is one EC2 for that same one-process stack. `terraform apply` created it. User-data stopped because `docker-compose.aws.yml` was not on main yet. `terraform destroy` removed the resources. No AWS resources from that apply are still running.
