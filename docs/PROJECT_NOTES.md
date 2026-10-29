# Project notes

How to run, operate and deploy the project. For what it is and why it's built this way, see the [README](../README.md).

- [Running locally](#running-locally)
- [API](#api)
- [Configuration](#configuration)
- [API keys](#api-keys)
- [Maintaining the knowledge base](#maintaining-the-knowledge-base)
- [Reviewing feedback](#reviewing-feedback)
- [Evals](#evals)
- [Load testing](#load-testing)
- [Deploying to AWS](#deploying-to-aws)
- [Troubleshooting](#troubleshooting)

## Running locally

**Everything in Docker** (closest to production, same image as AWS):

```bash
export ANTHROPIC_API_KEY=...
docker compose up --build app       # Postgres + schema + knowledge base + app on :8000
docker compose down                 # stop (keeps data); add -v to also delete the database
```

`--build` matters: the image contains a copy of the code, so without it Docker restarts the old version.

**App on the host, database in Docker** (fast iteration; requires Python 3.12):

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
docker compose up -d postgres
cd src
python -m scripts.seed              # load db/knowledge_base.json
uvicorn app.main:app --reload       # restarts on every save
```

**Without spending money:** set `MOCK_LLM=1` to replace the Claude call with a canned answer. Everything else is real. `/health` reports `"mock_llm": true` while it's on. In Docker: `docker compose run --rm --service-ports -e MOCK_LLM=1 app`.

| Local URL | What |
| --- | --- |
| http://localhost:8000 | Chat page |
| http://localhost:8000/docs | Interactive API docs (OpenAPI) |
| http://localhost:8000/health | Health check |

## API

| Endpoint | Behavior |
| --- | --- |
| `POST /query` | Answers a question. Returns the answer, the references used (question and statutes), a disclaimer and a `query_id`. `422` if declined, `429` with `Retry-After` over the rate limit, `503` if Claude is unavailable. |
| `POST /feedback` | Rates an answer (`up`/`down`, optional comment) by `query_id`. Records whether the rater was the public, a client key or an attorney key. |
| `GET /health` | Liveness check, used by the load balancer. |
| `GET /` | The chat page. |

Both POST endpoints accept an optional `X-API-Key` header; without one, the caller is the public, limited by IP address.

```bash
curl -X POST localhost:8000/query -H 'Content-Type: application/json' \
  -d '{"query": "My landlord still has my deposit 3 weeks after I moved out"}'
```

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | (required) | Claude API key. |
| `DATABASE_URL` | `postgresql://legal:legal@localhost:5432/legal` | Database connection. |
| `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME` | unset | Separate connection values. Take precedence over `DATABASE_URL` when `DB_HOST` is set; this is how ECS injects the RDS credentials, and it avoids URL-encoding a generated password. |
| `MOCK_LLM` | unset | `1` returns canned answers instead of calling Claude. Never in production. |
| `TRUST_PROXY_HEADERS` | unset | `1` behind a load balancer, so rate limits use the client's IP from `X-Forwarded-For`. Leave unset otherwise, or callers could spoof their identity. |
| `FASTEMBED_CACHE_PATH` | fastembed's default | Where the embedding model is cached. The Docker image sets it and downloads the model at build time. |

## API keys

The chat page and API work without a key (public, limited per IP). Keys are for programmatic clients and attorneys:

```bash
cd src
python -m scripts.api_keys create --name "Maria Lopez" --role attorney     # printed once; save it
python -m scripts.api_keys create --name "Load test" --role client --hourly-limit 1000000
python -m scripts.api_keys list
python -m scripts.api_keys revoke 3
```

In Docker: `docker compose exec app python -m scripts.api_keys ...`. Send the key in the `X-API-Key` header.

| Role | `/query` per hour | `/feedback` per hour | Notes |
| --- | --- | --- | --- |
| Public (per IP) | 20 | 30 | |
| Client | 200 | 200 | `--hourly-limit` overrides the `/query` limit |
| Attorney | 500 | 1,000 | Only attorney thumbs-up ratings become knowledge base drafts |

Limits are defined in [src/app/rate_limit.py](../src/app/rate_limit.py).

## Maintaining the knowledge base

[db/knowledge_base.json](../db/knowledge_base.json) is the source of truth. Edit it, then sync:

```bash
cd src && python -m scripts.seed
```

The sync adds new entries, updates changed ones and **deletes entries no longer in the file**. It's safe to re-run, and the running app picks up changes immediately with no restart.

- **`question`** is the entry's identity. Changing it creates a new entry and deletes the old one.
- **`aliases`** are other ways people ask the same thing. Add one when a real question misses its entry; it's usually a better fix than rewording the question.
- **`sources`** are shown to visitors as citations, so keep them exact.
- Keys starting with `_` (from the feedback export) are ignored.

After any change, run the retrieval eval (below). CI also runs it on every push.

## Reviewing feedback

```bash
cd src && python -m scripts.export_feedback                     # writes db/kb_candidates.json
docker compose exec app python -m scripts.export_feedback - > kb_candidates.json   # from Docker
```

It prints every thumbs-down answer with who rated it, their comments and the references the answer used. "None" flags a possible knowledge base gap. It also writes attorney thumbs-up answers that aren't already in the knowledge base as drafts, biggest gaps first, each with its closest existing entry. Close matches are better added as an alias of that entry than as a new one. The drafts file is git-ignored because it contains client questions.

## Evals

```bash
cd src
python -m scripts.eval_retrieval     # free, deterministic; exits 1 on any failure
python -m scripts.eval_answers --variant pilot \
  --ids deposit-late,probate-summary,emergency,prompt-injection,no-kb-coverage   # ~$0.15
python -m scripts.eval_answers       # all 26 cases, ~$0.50-$1.00
```

- Retrieval cases: [evals/retrieval_cases.json](../evals/retrieval_cases.json). Add a case whenever a real question misses.
- Answer cases: [evals/answer_cases.json](../evals/answer_cases.json), readable in [answer_cases.md](../evals/answer_cases.md).
- Answer eval results go to `.claude/hillclimb/answers/<variant>/`: `results.jsonl` (one row per case), `traces/` (full prompts and answers) and `errors.jsonl`. Re-running skips finished cases and retries failed ones. `--reps N` repeats each case to measure variance.
- The answer eval doesn't write to `query_log`; it calls `services.generate_answer` directly.

## Load testing

```bash
cd src
python -m scripts.api_keys create --name "Load test" --role client --hourly-limit 1000000
MOCK_LLM=1 uvicorn app.main:app --port 8000
# in another terminal, from the repo root (k6 installed, or via Docker as below)
k6 run -e API_KEY=jlg_... loadtest/k6.js
docker run --rm -v "$PWD/loadtest:/work/loadtest:ro" -v "$PWD/evals:/work/evals:ro" -w /work \
  grafana/k6 run -e BASE_URL=http://host.docker.internal:8000 -e API_KEY=jlg_... loadtest/k6.js
```

The script refuses to run unless `/health` reports mock mode, so it can't send paid traffic by accident. Afterwards, clean up: `DELETE FROM query_log WHERE model = 'mock';` and revoke the key.

## Deploying to AWS

Requires AWS credentials (`aws sts get-caller-identity` works), Docker running, and the project venv (for `aws-cdk-lib`). The CDK CLI runs through `npx`, so there's nothing to install globally.

```bash
source .venv/bin/activate
export ANTHROPIC_API_KEY=...
./scripts/deploy.sh          # ~15-20 minutes on the first run, mostly creating RDS
./scripts/destroy.sh         # ~10-15 minutes; deletes everything, including the data
```

`deploy.sh`:
1. Checks the key with a free Anthropic API call, so a bad key fails before anything is created.
2. Bootstraps CDK in the account and region the first time.
3. Runs `cdk deploy`, which shows the IAM and security group changes and asks for approval.
4. Stores the key in Secrets Manager and forces a new deployment, since ECS reads secrets at task start.
5. Waits for the service to stabilize, checks `/health` and prints the URL.

The region defaults to `us-east-2` (see Troubleshooting); set `AWS_REGION` to change it. `destroy.sh` leaves the `CDKToolkit` bootstrap stack in place for faster redeploys; it costs a few cents a month.

**Useful commands while deployed** (region `us-east-2`):

```bash
aws logs tail --follow $(aws logs describe-log-groups --query "logGroups[?contains(logGroupName,'LegalAssistanceStack')].logGroupName" --output text)
aws cloudformation describe-stacks --stack-name LegalAssistanceStack --query 'Stacks[0].Outputs'
```

The console's CloudFormation stack (Resources tab) lists every resource the deployment created.

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `fastembed` won't install | It needs Python 3.12 or 3.13; its ONNX runtime has no build for newer Pythons yet. Create the venv with `python3.12`. |
| `/query` returns `503` and the logs show `401 invalid x-api-key` | Anthropic rejected the key. Create one in the [Claude Console](https://console.anthropic.com/settings/keys) and restart with it. |
| `uvicorn: command not found` | The venv isn't active: `source .venv/bin/activate`. |
| `Connect call failed ... 5432` | Postgres isn't running: `docker compose up -d postgres`. |
| The page looks out of date | Rebuild the image (`docker compose up --build app`) and hard-refresh the browser (Cmd+Shift+R). |
| Deploy fails with `explicit deny in a service control policy` | The AWS account belongs to an Organization whose policy blocks that region. This project's account only allows `us-east-2`, which is why the scripts default to it. It can only be changed from the Organization's management account. |
| `export_feedback` fails in Docker with `Permission denied` | The container's user can't write to `/app/db`. Pass `-` and redirect: `... export_feedback - > kb_candidates.json`. |
| Schema changes didn't apply | `init.sql` only runs automatically on a new database. Apply it to an existing one with `python -m scripts.init_db`; it only adds what's missing. |
