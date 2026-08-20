# Formal PostgreSQL cutover ledger

Append one row per formal cutover attempt. Never record credentials or a full
connection string. A failed attempt is evidence and must not be removed.

| UTC date/time | Operator | Database identity | Release source commit | Release checksum | Root head | API head | Preserved row counts | Backup/snapshot reference | DB smoke Run ID (ready/create/replay/read/app recreation) | DB result | ECS deployed-service gate | Rollback reference | Notes |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `<YYYY-MM-DDTHH:MM:SSZ>` | `<name>` | `mta_data@pgm-uf6hl30c1vr9v8zr3o.pg.rds.aliyuncs.com` | `<40-char SHA>` | `<sha256>` | `7b8f3d1a2c4e` | `0003_plan_review_hardening` | `<table=count; ...>` | `<real snapshot ID/ticket>` | `<run-id + PASS/FAIL details>` | `<PASS/FAIL>` | `<NOT RUN/PASS/FAIL + deployment reference>` | `<config rollback/runbook reference>` | `<observations>` |
| `2026-08-20T15:24:09Z` | `wangxing` | `mta_data@pgm-uf6hl30c1vr9v8zr3o.pg.rds.aliyuncs.com` | `5e7bb6a631342f99d1d3c6cff6be736f88aede34` | `f10e335d47387b527044e9429a2b316d99fcda7af0ae2495ba0ff138eafa9d0c` | `7b8f3d1a2c4e` | `0003_plan_review_hardening` | `all preexisting tables preserved; clients=2; ontology_reviews=2; ontology_review_items=2; plan_snapshots=1; plan_items=1; mta_input=11147; synthetic_user_event=11147; mta_sim_budget_observation=100000; mta_sim_outcome_observation=107000; simulation_ground_truth=7171; API smoke rows returned to zero` | `pgm-uf6hl30c1vr9v8zr-full-snapshot-20260819-202639` | `cutover-20260820-01: ready=PASS; create=201; replay=201 same response; app recreation=PASS; read=200; run-scoped cleanup=zero` | `PASS` | `NOT RUN — ECS/systemd deployment gate remains separate` | `restore snapshot pgm-uf6hl30c1vr9v8zr-full-snapshot-20260819-202639; docs/operations/pg-cutover-ledger.md` | `Formal database gate verified. Root duplicate ancestor removed transactionally; independent API ledger established. R5 remains PENDING_HUMAN_REVIEW.` |

## Guarded migration procedure

The formal database is shared with algorithm tables. Extra tables are expected;
the tool audits them for row-count preservation rather than treating them as API
assets. It strictly validates the 13 root tables, root-head columns, both
ledgers, and all three API tables.

The only migratable starting state is: root ledger at `7b8f3d1a2c4e`, optionally
with known stale ancestor `da19a197a9f7`; all API tables absent; and
`api_alembic_version` absent or empty. Partial API schemas and other ledger
states fail closed.

After recording a usable snapshot reference, set all four guards in the same
PowerShell session. Never store their values in a file:

```powershell
$env:ALLOW_API_LEDGER_RECONCILE = "1"
$env:ALLOW_FORMAL_API_LEDGER_RECONCILE = "mta_data"
$env:MTA_DATA_BACKUP_CONFIRMED = "1"
$env:MTA_DATA_BACKUP_REFERENCE = "<snapshot-or-ticket-reference>"
```

Formal `--apply` acquires a transaction-scoped advisory lock before its first
audit. In one transaction it removes only the known stale root ancestor when
present, runs API migrations through independent `api_alembic_version`, and
re-audits every protected object. Any mismatch raises and rolls back the whole
transaction. It never drops, truncates, or rebuilds an existing table.

## Completion gates

Do not declare the cutover complete from a successful migration command alone.
Run the post-cutover verifier first without `--smoke`. It requires the exact
root and API heads, the complete API schema, the immutable 2.1 release bundle,
and pending R5 identity. This mode is read-only:

```powershell
& ".\ontology review api\.venv\Scripts\python.exe" `
  -m scripts.verify_formal_pg_cutover
```

The approved cutover scope contains no SQLite business-data transfer. This is a
specification decision, not something the verifier discovers at runtime.
Concepts and rules remain authoritative in the verified Git bundle; zero rows
in the legacy `concepts` and `rules` tables are therefore not a migration
failure. If a later runtime inventory finds another SQLite business source,
stop and amend the scope before declaring completion.

The database persistence smoke is separately guarded because it writes rows to
the formal database. Stop/quiesce all application writers first. Choose and
record a deterministic Run ID; it makes interrupted rows recoverable. The
script removes only that Run ID's rows, proves no dependent rows remain, and
treats whole-database count drift as a concurrency alarm. Smoke and recovery
hold the same PostgreSQL session advisory lock from precheck through final
verification, so two verifier runs cannot overlap:

```powershell
$env:ALLOW_FORMAL_POSTGRES_SMOKE = "mta_data"
$env:ALLOW_FORMAL_POSTGRES_MAINTENANCE = "mta_data"
$env:MTA_DATA_BACKUP_CONFIRMED = "1"
$env:MTA_DATA_BACKUP_REFERENCE = "pgm-uf6hl30c1vr9v8zr-full-snapshot-20260819-202639"
& ".\ontology review api\.venv\Scripts\python.exe" `
  -m scripts.verify_formal_pg_cutover --smoke --run-id cutover-20260820-01
```

If the shell or process is interrupted, do not invent a new Run ID. Recover it
first with the same guards. Cleanup proves only that recovery succeeded; it
does not produce the database-cutover completion marker:

```powershell
& ".\ontology review api\.venv\Scripts\python.exe" `
  -m scripts.verify_formal_pg_cutover --cleanup-run-id cutover-20260820-01
```

Database completion requires `release audit: PASS`, `schema audit: PASS`,
`database persistence/app-recreation smoke: PASS`, and
`FORMAL_DATABASE_CUTOVER_VERIFIED: PASS`. This proves the formal database gate
only. It does **not** restart or probe the deployed ECS/systemd service. Record
`ECS deployed-service gate = NOT RUN` until a later cold service restart and
external HTTP `/ready` + create/replay/read check passes against the deployed
configuration.
