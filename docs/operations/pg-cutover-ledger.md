# Formal PostgreSQL cutover ledger

Append one row per formal cutover attempt. Never record credentials or a full
connection string. A failed attempt is evidence and must not be removed.

| UTC date/time | Operator | Database identity | Release source commit | Release checksum | Root head | API head | Preserved row counts | Backup/snapshot reference | Smoke (ready/create/replay/read/restart) | Result | Rollback reference | Notes |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `<YYYY-MM-DDTHH:MM:SSZ>` | `<name>` | `mta_data@<non-secret host/instance>` | `<40-char SHA>` | `<sha256>` | `7b8f3d1a2c4e` | `0003_plan_review_hardening` | `<table=count; ...>` | `<snapshot ID/ticket>` | `<PASS/FAIL details>` | `<PASS/FAIL>` | `<config rollback/runbook reference>` | `<observations>` |
