# Formal PostgreSQL cutover ledger

Append one row per formal cutover attempt. Never record credentials or a full
connection string. A failed attempt is evidence and must not be removed.

| UTC date/time | Operator | Database identity | Release source commit | Release checksum | Root head | API head | Preserved row counts | Backup/snapshot reference | Smoke (ready/create/replay/read/restart) | Result | Rollback reference | Notes |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `<YYYY-MM-DDTHH:MM:SSZ>` | `<name>` | `mta_data@<non-secret host/instance>` | `<40-char SHA>` | `<sha256>` | `7b8f3d1a2c4e` | `0003_plan_review_hardening` | `<table=count; ...>` | `<snapshot ID/ticket>` | `<PASS/FAIL details>` | `<PASS/FAIL>` | `<config rollback/runbook reference>` | `<observations>` |

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

The repository contains no real SQLite business data to import. Concepts and
rules remain authoritative in the verified Git bundle; zero rows in the legacy
`concepts` and `rules` tables are therefore not a migration failure.

The final create/replay/read/restart smoke is separately guarded because it
writes uniquely identified rows to the formal database. It removes only those
rows and then requires every table count to equal its pre-smoke value:

```powershell
$env:ALLOW_FORMAL_POSTGRES_SMOKE = "mta_data"
$env:MTA_DATA_BACKUP_REFERENCE = "<snapshot-reference>"
& ".\ontology review api\.venv\Scripts\python.exe" `
  -m scripts.verify_formal_pg_cutover --smoke
```

Completion requires `release audit: PASS`, `schema audit: PASS`, and
`formal smoke: PASS (... cleanup=exact)`. Record all three in the ledger row.
