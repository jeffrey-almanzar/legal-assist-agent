# Legal Information Assistant with Claude and pgvector

[![CI](https://github.com/jeffrey-almanzar/legal-assist-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/jeffrey-almanzar/legal-assist-agent/actions/workflows/ci.yml)

A legal information assistant for a (fictional) Miami law firm. It answers common Florida law questions in plain language, grounded in firm-approved answers retrieved with **pgvector**, and cites the statutes each answer relies on. A **FastAPI** backend on **PostgreSQL** and the **Claude API**, deployed to **AWS ECS Fargate and RDS** with the **AWS CDK**.

**Stack:** Python 3.12 · FastAPI · Claude API (Opus 5.5) · PostgreSQL 16 + pgvector · fastembed (`bge-small-en-v1.5`) · AWS CDK (Python) · ECS Fargate · RDS · ALB · Secrets Manager · Docker · GitHub Actions · k6 · pytest

## At a glance

| | |
| --- | --- |
| **What it does** | Embeds each question locally, finds up to 4 firm-approved answers in PostgreSQL by cosine distance, and has Claude answer from them, citing the statutes they rely on. Out-of-scope questions get a referral, emergencies get "call 911" first. |
| **Knowledge base** | 56 entries across 10 Florida practice areas, 96 searchable phrasings, 47 with statute citations, kept in git and synced to the database. |
| **Retrieval accuracy** | On 40 labeled questions, the right entry reaches Claude for **32 of 32** on-topic questions, and **8 of 8** off-topic ones get no references. Measured and tuned with an eval that gates CI. |
| **Cost and abuse control** | Hourly rate limits per IP or API key, stored in Postgres so they hold across instances; hashed API keys with client and attorney roles. |
| **Feedback loop** | Every answer is logged and can be rated; attorney thumbs-up answers become draft knowledge base entries for review. |
| **Tests and CI** | 27 tests; GitHub Actions runs them, the retrieval eval against a real pgvector database, a Docker build and a CDK synth on every push. |
| **Performance** | 343 requests/s with p95 latency of 199 ms and 0 errors across 37,727 requests (app stack only, Claude mocked, one process on a laptop). |
| **Deployed** | One script deploys 39 AWS resources with CDK: Fargate behind a load balancer, RDS PostgreSQL with pgvector, secrets in Secrets Manager. About $0.10/hour; one command tears it all down. |

<p>
  <img src="docs/images/chat-home.png" width="56%" alt="The chat page served from AWS: the firm header, the question box with common questions, and a practice areas panel">
  <img src="docs/images/chat-answer.png" width="41%" alt="The assistant answering a security deposit question: it applies the 15-day and 30-day deadlines to &quot;3 weeks&quot;, lists next steps, and shows the firm-approved answers it consulted with their Florida statutes in the margin">
</p>
<sub>The deployed app on AWS (left), and an answer (right) that works the user's "3 weeks" against both statutory deadlines, with the firm-approved answers it retrieved and their statutes in the margin.</sub>

## Contents

- [The problem](#the-problem)
- [Key decisions and trade-offs](#key-decisions-and-trade-offs)
- [Architecture](#architecture)
- [Engineering decisions](#engineering-decisions)
- [Evaluation](#evaluation)
- [Testing, CI and performance](#testing-ci-and-performance)
- [Deployment on AWS](#deployment-on-aws)
- [Quick start](#quick-start)
- [Limitations and next steps](#limitations-and-next-steps)
- [Repository map](#repository-map)

## The problem

Law firms field the same questions over and over: how long a divorce takes, when a landlord must return a deposit, what a first consultation costs. Answering them takes staff and attorney time, and a slow answer loses the client.

A chatbot that answers from a general-purpose model is the wrong tool for a law firm:

- **It can be confidently wrong.** Florida law changes every year, and a model's training data goes stale. Checking this project's content against current sources found four 2026 changes, such as the small-estate probate limit doubling to $150,000, and a widely reported eviction change that never actually passed.
- **The firm can't vouch for it.** Answers need to come from content the firm's attorneys have reviewed, with the statutes behind them.
- **Some questions must not be answered like the others.** "Will I win my case?", "should I accept this settlement?" and "my ex is at my door right now" each need a specific, safe response.
- **Every answer costs money,** so a public page needs limits.

This project grounds every answer in an attorney-reviewable knowledge base, measures retrieval instead of eyeballing it, and builds in the guardrails and feedback loop a firm would need.

## Key decisions and trade-offs

| Decision | Instead of | Why |
| --- | --- | --- |
| pgvector inside the app's PostgreSQL | A separate vector database | One store to run, secure and back up, alongside the logs, keys and rate limits; an HNSW index is plenty at this scale. |
| Local embeddings with fastembed | An embeddings API | No per-query cost and no second vendor; the model is baked into the image, so containers embed without network access. |
| Retrieval tuned against a labeled eval | Picking a similarity cutoff by feel | The eval rejected an approach that seemed better, and later caught a real regression ([details](#retrieval-measured-not-assumed)). |
| Grounded prompts with firm-approved references | Letting the model answer from its own knowledge | Answers stay consistent with reviewed content and cite statutes the firm has vetted. |
| ECS Fargate | AWS Lambda | The embedding model loads once per container and stays in memory with a warm database pool, instead of reloading on every cold start. |
| Public subnets for the app, no NAT gateway | NAT gateways | Saves about $65/month; the app's security group only admits traffic from the load balancer, and the database sits in isolated subnets. |
| Secrets injected by ECS at task start | Keys in environment variables or the template | The Anthropic key and database password never appear in code, the image or CloudFormation. |
| Knowledge base as a JSON file in git | Editing content directly in the database | Every change to what the assistant knows is reviewable like code; the seed script makes the database match the file. |

## Architecture

![Sequence diagram: the visitor posts a question; the FastAPI app counts it against the rate limit in PostgreSQL, embeds it with the local fastembed model, finds the nearest phrasings with pgvector, receives up to four firm-approved answers within distance 0.37 with their statutes, sends them with the question to the Claude API, gets the answer (with server-side fallback on refusal), logs the query, and returns the answer with citations and a query ID](docs/diagrams/architecture.svg)

| Component | Role |
| --- | --- |
| **FastAPI app** ([src/app/](src/app/)) | The chat page, the API, rate limiting and API key auth, retrieval, the Claude call and query logging. Stateless, so it scales horizontally. |
| **fastembed** | Runs the `BAAI/bge-small-en-v1.5` embedding model locally (384 dimensions). |
| **PostgreSQL + pgvector** | The knowledge base and its embeddings (HNSW index), plus the query log, feedback, API keys and rate limit windows. |
| **Claude API** | Claude Opus 5.5 writes the answer from the retrieved references. Server-side fallback reroutes a safety refusal to another model within the same call. |

### The knowledge base and feedback loop

![Feedback loop: db/knowledge_base.json is synced into PostgreSQL by scripts.seed; the chat page retrieves from it for every answer; scripts.export_feedback turns ratings into a thumbs-down report and draft entries from attorney thumbs-up ratings; an attorney reviews the drafts, adds statutes and promotes them back into the JSON file](docs/diagrams/knowledge-loop.svg)

Each entry in [db/knowledge_base.json](db/knowledge_base.json) has a category, a question, alternate phrasings, the approved answer and its sources:

```json
{
  "category": "landlord_tenant",
  "question": "When does my landlord have to return my security deposit in Florida?",
  "aliases": ["My landlord kept my security deposit. What can I do?"],
  "answer": "If the landlord isn't keeping any of the deposit, they must return it within 15 days...",
  "sources": ["Fla. Stat. § 83.49"]
}
```

Every query is logged, including refusals and errors, with the references used, the model that answered, token counts and time spent on retrieval and on Claude. Anyone can rate an answer, but only an **attorney's** thumbs-up turns it into a draft entry. The export also reports each draft's closest existing entry, so near-duplicates become aliases instead of new entries. Query IDs are random UUIDs, so a caller can't rate other people's answers by guessing IDs.

## Engineering decisions

### Retrieval: measured, not assumed

Retrieval was tuned against a labeled set of realistic questions, worded the way people actually ask ("landlord turned off my water", "got a DUI, will I lose my license"):

| Iteration | Ranked first | Reached Claude | Off-topic kept out |
| --- | --- | --- | --- |
| Embed questions only, cutoff 0.35, top 3 | 23/31 | 28/31 | 3/3 |
| Embed question + answer text (rejected) | 26/31 | 27/31 | 3/3 |
| + alternate phrasings, cutoff 0.37, top 4 | 28/32 | 32/32 | 2/2 |
| Current, with 8 off-topic cases | **29/32** | **32/32** | **8/8** |

- **Embedding the answer text made retrieval worse,** not better: matches got farther away, so a usable cutoff would have let off-topic questions through. Questions are embedded alone.
- **Alternate phrasings fixed the real misses.** Nobody asks "How does eviction for unpaid rent work?"; they say "I got a 3-day notice on my door". Each phrasing gets its own embedding, and a match on any of them retrieves the entry.
- **The cutoff is calibrated, and the margin is stated.** Real matches scored up to 0.37 and the closest off-topic question 0.385, so the cutoff is 0.37, and the eval is what will catch a regression.
- **The eval caught a regression for real:** renaming the firm's city made "what's the weather in Miami" match "Do you handle cases outside Miami?". The entry was rephrased around what people actually ask.

### Grounding and safety

- **The model answers from vetted content.** Retrieved answers are sent as references with their statutes; the system prompt says to base the answer on them, not contradict them, and cite the statute where it helps. Time-sensitive facts in the content were checked against current sources before use.
- **Claude caught a data bug.** Early sample entries began with "SAMPLE ENTRY - replace with your firm's details", and Claude correctly refused to treat them as confirmed facts. The marker moved into a field that never reaches the model.
- **Scope and safety are explicit:** Florida only, no outcome predictions or personal advice, "call 911" first in emergencies, referrals for practice areas the firm doesn't handle, and a "not legal advice" disclaimer on every answer.
- **Failures are deliberate responses:** a refusal returns `422` and a Claude outage or bad key returns `503` instead of a raw `500`; the chat page turns each into a message with the firm's phone number.

### Controlling cost and abuse

- **Rate limits live in Postgres,** as one-hour windows per caller, so they hold across multiple app instances: 20 questions/hour per IP for the public, more for API keys, with a per-key override (used for load testing).
- **API keys are random 32-byte tokens stored only as SHA-256 hashes,** shown once at creation. A wrong key gets `401` rather than silently falling back to public access.
- **Behind the load balancer, limits use the real client IP,** read from the last `X-Forwarded-For` entry only when a trusted proxy sets it, since earlier entries can be spoofed.
- **Housekeeping never fails a request:** expired windows are cleaned up on 1% of requests, best-effort. This was a real bug: a failed cleanup turned into a `500` for the visitor, and made CI fail intermittently until it was reproduced and fixed.

## Evaluation

```bash
cd src
python -m scripts.eval_retrieval   # free: no Claude calls; exits 1 on any failure (runs in CI)
python -m scripts.eval_answers     # paid: ~26 answers + 26 gradings, about $0.50-$1.00
```

**Retrieval eval** ([evals/retrieval_cases.json](evals/retrieval_cases.json)): 40 questions, each labeled with the entry that should reach Claude, or none for off-topic ones. Current result: 32/32 on-topic, 8/8 off-topic. The questions were written alongside the knowledge base, so real traffic will be harder; the query log is how to keep measuring it.

**Answer eval** ([evals/answer_cases.md](evals/answer_cases.md)): 26 cases, 17 common and 9 adversarial (out of scope, another state, an emergency in progress, "will I win?", "should I accept this settlement?", an instruction-override attempt, a question with no knowledge base entry, a false premise and a question in Spanish). Each answer runs through the app's real retrieval and prompt, then **Claude Sonnet 5.5** grades it, never the model under test, with schema-validated output on four checks: faithful to the references, key facts present (scored as a fraction), no fabricated or misattributed citations, and safe and in scope. The runner saves results as they complete, resumes after a crash, separates errors from failures and reports a 95% confidence interval. It has been tested end to end with stubbed models; the first paid run is pending.

## Testing, CI and performance

**27 tests** (`pytest -q`, no database or API key needed) cover the API and its error codes, auth and rate limits (including a spoofed `X-Forwarded-For`), prompt construction and query logging. [GitHub Actions](.github/workflows/ci.yml) runs four jobs on every push and pull request, none of which need secrets: the tests, the **retrieval eval against a fresh pgvector database**, a Docker build and a CDK synth.

**Load test:** [k6](loadtest/k6.js) sends real questions from the eval set to `POST /query`, ramping to 50 concurrent users, against `MOCK_LLM=1`. That measures the app's own stack (rate limiting, embedding, vector search and logging) without paying for Claude calls, and the script refuses to run against a server that would call Claude. On an Apple M1 Pro, one Uvicorn process, Postgres in Docker:

| Requests | Throughput | Median | p95 | Max | Errors |
| --- | --- | --- | --- | --- | --- |
| 37,727 | **343/s** | **109 ms** | **199 ms** | 406 ms | **0** |

In production, Claude's answer takes seconds and dominates response time; the surrounding stack adds about a tenth of a second and isn't the bottleneck.

## Deployment on AWS

Everything is in [src/infra/cdk_stack.py](src/infra/cdk_stack.py). [scripts/deploy.sh](scripts/deploy.sh) verifies the API key with Anthropic, bootstraps CDK if needed, deploys, stores the key in Secrets Manager and restarts the app; [scripts/destroy.sh](scripts/destroy.sh) removes everything.

![AWS deployment in us-east-2: visitors reach an Application Load Balancer in public subnets, which forwards to an ECS Fargate task (ARM64, 1 vCPU, 2 GB) running the same Docker image as locally; the task reaches RDS PostgreSQL 16 with pgvector in isolated subnets, gets its secrets from Secrets Manager at start, pulls its image from ECR, writes to CloudWatch Logs, and calls the Claude API over its public IP; there are no NAT gateways](docs/diagrams/aws-deployment.svg)

| Concern | Choice |
| --- | --- |
| Compute | ECS Fargate on Graviton (ARM64), behind an Application Load Balancer health-checking `/health`. The same image as local development; deployments roll back automatically if the new task never becomes healthy. |
| Database | RDS PostgreSQL 16 (`db.t4g.micro`) with pgvector, encrypted, in isolated subnets with no internet route; only the app's security group can reach it. |
| Secrets | The database password is generated by AWS; the Anthropic key is stored after the first deploy. ECS injects both at task start. |
| Cost and teardown | About $0.10/hour. Database with no final snapshot, one-day log retention, everything set to delete with the stack. |

Deployed as a same-day proof of concept on October 29, 2026. The CloudFormation stack with all 39 resources created, and the load balancer's health check reporting the app healthy:

![CloudFormation console: LegalAssistanceStack in CREATE_COMPLETE with 39 resources, alongside the CDKToolkit bootstrap stack](docs/images/aws-cloudformation.png)

![Target group: 1 healthy target on port 8000, 0 unhealthy](docs/images/aws-target-group.png)

<details>
<summary><b>More from the deployment</b>: the ECS service, startup logs and the RDS database</summary>

The app running on Fargate, 1 of 1 tasks:

![ECS console: the cluster with 1 active service and 1 running task](docs/images/aws-ecs-service.png)

On startup the container applies the schema and loads the knowledge base into RDS, then serves traffic:

![CloudWatch Logs: "Applied /app/db/init.sql", "Synced 56 entries (96 phrasings)", Uvicorn startup, then health checks and page requests returning 200](docs/images/aws-cloudwatch-logs.png)

PostgreSQL 16.13 on `db.t4g.micro`, encrypted, gp3 storage:

![RDS console: the database available, PostgreSQL 16.13, db.t4g.micro, encryption enabled, 20 GiB gp3 storage](docs/images/aws-rds.png)

</details>

## Quick start

Requires Docker and an [Anthropic API key](https://console.anthropic.com/settings/keys).

```bash
export ANTHROPIC_API_KEY=...
docker compose up --build app      # starts PostgreSQL, applies the schema, loads the knowledge base
```

Then open http://localhost:8000, or the interactive API docs at http://localhost:8000/docs. The API reference, configuration, mock mode, API keys, evals and deployment steps are in [docs/PROJECT_NOTES.md](docs/PROJECT_NOTES.md).

## Limitations and next steps

Deliberate scope limits for a portfolio project, and what a production version would add:

- **The firm and its knowledge base are fictional.** The content was fact-checked, but a real deployment needs a licensed Florida attorney to review every entry.
- **The answer eval hasn't had its first paid run.** Next: run it, review the grades by hand, then tune the prompt against it.
- **HTTP only, one app task and a single-AZ database.** Production would add HTTPS (a domain and an ACM certificate), at least two tasks across zones, a Multi-AZ database with backups, and schema changes as a one-off deploy step.
- **No retention policy on the query log,** which stores client questions indefinitely. A firm would want scheduled deletion and a privacy review.
- **Single-turn and English-first.** Each question is answered on its own, and the embedding model is English-only, so retrieval for Spanish questions is likely weaker.

## Repository map

| Path | Contents |
| --- | --- |
| [src/app/services.py](src/app/services.py) | Retrieval plus the Claude call, the system prompt, query logging and mock mode |
| [src/app/db.py](src/app/db.py) | Connection pool, pgvector search, knowledge base sync, logs, feedback, API keys, rate limit windows |
| [src/app/auth.py](src/app/auth.py) · [rate_limit.py](src/app/rate_limit.py) | API key auth with roles; per-caller hourly limits |
| [src/app/routers/](src/app/routers/) | `POST /query` and `POST /feedback` |
| [src/scripts/](src/scripts/) | `seed`, `init_db`, `api_keys`, `export_feedback`, `eval_retrieval`, `eval_answers` |
| [src/infra/](src/infra/) | The AWS CDK stack |
| [db/](db/) | Schema and the knowledge base |
| [evals/](evals/) · [tests/](tests/) · [loadtest/](loadtest/) | Eval cases, unit tests, load test |
| [scripts/](scripts/) | `deploy.sh` and `destroy.sh` |
| [docs/](docs/) | Maintainer notes, diagram sources ([D2](https://d2lang.com)) and screenshots |

---

<sub>Jeff Legal Group is a fictional firm created for this project. The assistant provides general information about Florida law, not legal advice.</sub>
