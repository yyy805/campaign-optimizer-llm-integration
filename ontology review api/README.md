# Ontology Review API

This is the permanent HTTP foundation for the Demo's Ontology review path. It loads the canonical `v1.1-demo` package, evaluates submitted MTA concepts independently, persists an immutable Review, and can run unchanged on a laptop or Alibaba Cloud ECS.

It does **not** modify MTA data, budgets, or Ontology files. Feedback, governance, local-KB evidence refresh, Agent integration, and frontend integration extend this service in later increments.

## What this increment provides

- deterministic R1–R7 evaluation (R7 is recognized as `RETIRED`);
- generic G1–G2 action guardrails;
- the approved R1-over-R2 conflict decision only;
- client A/B automatic-action limits;
- `MATCH`, `CONFLICT`, and `NO_COVERAGE` outcomes;
- immutable request, evaluation, identity, version, checksum, and timestamp provenance;
- tenant-scoped API-key identity;
- transactional SQLite storage, Alembic migrations, and idempotent writes;
- health, readiness, version, create/get/list APIs and OpenAPI docs;
- Docker/ECS packaging, persistent data, smoke test, backup, and safe restore.
- a campaign-optimizer-compatible plan-review adapter backed only by the canonical Ontology.

## API contract

Public operational endpoints:

```text
GET /health
GET /ready
GET /docs                 # configurable
```

Authenticated v1 endpoints:

```text
GET  /api/v1/ontology/version
POST /api/v1/reviews
GET  /api/v1/reviews/{review_id}
GET  /api/v1/reviews
POST /api/v1/plan-reviews
GET  /api/v1/plan-reviews/{review_id}
```

Business requests use `X-API-Key`. `POST /api/v1/reviews` also requires `Idempotency-Key`. Identity and tenant come from the API key configuration; callers cannot assert either value.

### Create a Review

```bash
curl -X POST http://localhost:8000/api/v1/reviews \
  -H 'X-API-Key: demo-agent-key' \
  -H 'Idempotency-Key: r3-demo-001' \
  -H 'Content-Type: application/json' \
  -d '{
    "client_id":"demo_client_001",
    "entity":{"grain":"touchpoint","id":"SP:TOP_OF_SEARCH"},
    "candidate_rules":["R3"],
    "inputs":[{"concept":"mta_roas","value":1.6,"baseline":1.0}],
    "expected_ontology_version":"v1.1-demo"
  }'
```

`candidate_rules` is only the caller's claimed evaluation scope. The API still evaluates each condition from `inputs`; it never trusts a claimed match. Omit the field to evaluate every active rule whose complete required input set is present.
Rules are evaluated only at their canonical entity grain (`campaign` or `touchpoint`), and numeric inputs must satisfy the concept card's finite value range.

Stable business errors include `AUTH_REQUIRED`, `UNKNOWN_RULE`, `UNKNOWN_CONCEPT`, `MISSING_REQUIRED_METRIC`, `ONTOLOGY_VERSION_MISMATCH`, `IDEMPOTENCY_CONFLICT`, and `REVIEW_NOT_FOUND`. Every response returns `X-Request-ID`; error bodies include the same correlation ID.

### Review a campaign-optimizer final plan

`POST /api/v1/plan-reviews` accepts the other team's complete `final_plan` JSON, including `review_evidence`. It selects rules server-side, evaluates only `../docs/ontology/ontology 概念卡`, and returns their `ontology_review` exchange shape (`SUPPORT`, `CONFLICT`, `NOT_APPLICABLE`, `INSUFFICIENT_EVIDENCE`, or `UNVERIFIED`). It never loads the divergent `campaign_optimizer/ontology` copy and never executes or rewrites a plan.

```bash
curl -X POST http://localhost:8000/api/v1/plan-reviews \
  -H 'X-API-Key: demo-agent-key' \
  -H 'Idempotency-Key: plan-demo-001' \
  -H 'Content-Type: application/json' \
  --data-binary @../docs/campaign-optimizer-llm-integration-main/tests/fixtures/plan_a/final_plan.demo.json
```

The shared `final_plan` schema has no `client_id`, so the API uses the server-owned `PLAN_REVIEW_CLIENT_ID` setting (`demo_client_001` by default). The LLM team's remaining handoff is an HTTP client that posts the plan, validates the returned review, and supplies both objects to `LocalLLMOrchestrator`. Their authoritative-review validator must also be pointed at the canonical rule root; no Qwen call is needed for this contract handoff.

## Run locally

Python 3.12 is the deployment baseline:

```bash
cd "ontology review api"
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[test]'
cp .env.example .env
```

For local execution, edit `.env` so `ONTOLOGY_PATH` points to `../docs/ontology/ontology 概念卡`, set a non-placeholder API key, then run:

```bash
alembic upgrade head
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000/docs`. Run verification with:

```bash
python -m pytest
SMOKE_API_KEY=your-key ./scripts/smoke.sh
```

## Docker and ECS

Copy `.env.example` to `.env` and replace every `change-me` credential. From this directory:

```bash
docker compose build
docker compose up -d
SMOKE_API_KEY=your-key ./scripts/smoke.sh
docker compose restart
SMOKE_API_KEY=your-key ./scripts/smoke.sh --verify-persistence
```

The image is fixed as `ontology-review-api:0.1.0`, runs one worker (SQLite is single-instance), uses `/data` as a persistent volume, and mounts `/ontology` read-only. Startup applies committed migrations. `/health` proves the process is alive; `/ready` returns 200 only when the validated Ontology snapshot and database are both available.

### PolarDB for PostgreSQL deployment

The production database is selected only by `DATABASE_URL`; Compose no longer overrides it. Keep the SQLite value for local use. For ECS, create a dedicated database and least-privilege application account, then put a URL like this in the server-only `.env` (URL-encode special characters in the username and password):

```text
DATABASE_URL=postgresql+psycopg://review_api:ENCODED_PASSWORD@PRIVATE_CLUSTER_ENDPOINT:5432/ontology_review?connect_timeout=5
```

If SSL is enabled for the selected PolarDB endpoint, use the SSL parameters required by the team's certificate policy, for example `?sslmode=require`. Do not guess this setting: obtain it from the PolarDB operator and use certificate verification when their policy requires it.

Characters such as `@`, `:`, `/`, `?`, `#`, and `%` in credentials must be percent-encoded. For example, `p@ss%word` becomes `p%40ss%25word`. Generate the encoded value locally with a trusted URL builder; do not paste a real password into an online encoder. Keep `connect_timeout` between 1 and 60 seconds so DNS or network failures cannot block startup indefinitely.

Before starting the container, the Alibaba Cloud operator must provide or confirm all of the following:

1. PolarDB is the PostgreSQL edition and is running.
2. ECS and PolarDB are in the same region and VPC; use the private **cluster endpoint**.
3. The PolarDB whitelist contains the ECS private IP, or the ECS security group is attached to the cluster.
4. A non-system database such as `ontology_review` and a dedicated account exist. Do not use the default `postgres` database for application objects.
5. The account can connect and create/alter tables and indexes during Alembic migration. After migration, privileges can be narrowed only if future migrations have an approved elevated-credential procedure.
6. Port 5432 is not opened to the public internet. The API port is restricted to approved Agent/frontend networks.
7. The real password is stored only in the ECS `.env`/secret configuration and never committed or pasted into tickets, screenshots, or test output.

Alibaba Cloud references: [connect to PolarDB for PostgreSQL](https://www.alibabacloud.com/help/en/polardb/polardb-for-postgresql/connect-to-polardb/), [configure the cluster whitelist](https://www.alibabacloud.com/help/en/polardb/polardb-for-postgresql/set-ip-address-whitelists-for-a-cluster), and [choose an endpoint](https://www.alibabacloud.com/help/en/polardb/polardb-for-postgresql/view-or-apply-for-an-endpoint).

From the ECS checkout:

```bash
cd "ontology review api"
docker compose build
docker compose up -d
docker compose ps
curl --fail http://127.0.0.1:8000/health
curl --fail http://127.0.0.1:8000/ready
SMOKE_API_KEY=your-agent-key ./scripts/smoke.sh
docker compose restart
SMOKE_API_KEY=your-agent-key ./scripts/smoke.sh --verify-persistence
```

Startup runs `alembic upgrade head` against the configured database. A repeated startup at the current migration head is safe. If PolarDB is unreachable or its migration/schema is incomplete, `/health` remains available but `/ready` returns 503 and business writes remain unavailable.

For a one-time integration test against a **disposable test database**, not a shared production database:

```bash
ALLOW_POSTGRES_TEST_MIGRATION=1 \
TEST_POSTGRES_URL='postgresql+psycopg://review_api:ENCODED_PASSWORD@PRIVATE_CLUSTER_ENDPOINT:5432/ontology_review_test?connect_timeout=5' \
  .venv/bin/python -m pytest tests/test_postgres_integration.py -q
```

The safety gate requires both `ALLOW_POSTGRES_TEST_MIGRATION=1` and a database name ending in `_test`. The test creates uniquely identified review rows, verifies migration, idempotent replay, conflicting replay and restart persistence, then deletes only those exact tenant/principal/key/ID rows. Without `TEST_POSTGRES_URL`, it is reported as skipped; SQLite cannot be used as proof that PolarDB works.

If readiness fails, ask the operator for the container logs and `X-Request-ID`, then check cluster state, private endpoint/port, VPC, whitelist/security group, account/database, password URL encoding, SSL policy, and Alembic permissions—in that order. Never paste a resolved Compose configuration or connection URL containing the password into shared chat.

For ECS, install Docker with Compose, copy the repository and a server-only `.env`, then run the same commands. Configure the ECS security group to allow port 8000 only from approved Agent/frontend networks. Prefer an HTTPS reverse proxy; do not expose SQLite, `.env`, or unrestricted `/docs` publicly. Set `DOCS_ENABLED=false` when interactive docs are not needed.

Before an upgrade:

```bash
DATABASE_PATH=./data/review.db BACKUP_DIR=./backups ./scripts/backup.sh
```

The Compose named volume is inside Docker, so an ECS operator should either run the backup script inside a maintenance container with that volume or configure an explicit host bind mount. `restore.sh` deliberately refuses to overwrite a target: restore to a new file, run `/ready` and smoke tests, then switch the configured database path. Keep the current and previous versioned images for rollback.

## Important limits

- SQLite supports one API instance and one Uvicorn worker. PolarDB removes that storage limitation, but this Demo image intentionally stays at one API worker until load, connection-pool, and concurrency behavior are measured.
- Demo API keys are basic shared secrets, not enterprise identity. Store them in ECS secret configuration and rotate them before sharing the address.
- The current evidence status is `AVAILABLE` when stable evidence references are supplied, otherwise `PENDING`; live local-KB refresh arrives in the evidence integration increment.
- Feedback, status transitions, and governance endpoints listed in `BUILD_PLAN.md` are intentionally added on this same schema/service next; no replacement project is planned.
