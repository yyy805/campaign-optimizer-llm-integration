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
