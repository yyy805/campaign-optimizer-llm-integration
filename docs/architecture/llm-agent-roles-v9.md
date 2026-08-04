# Local three-agent workflow v9: temporary model mapping

V9 temporarily uses generally available Model Studio models while the
`qwen3.8-max-preview` Reviewer entitlement is checked on 2026-08-05.

| Role | Temporary v9 model | Intended later mapping |
| --- | --- | --- |
| Triage | `qwen3.6-flash` | Reassess after smoke evidence |
| Executor | `qwen3.7-max` | unchanged |
| Reviewer | `qwen3.7-plus` | revert to `qwen3.8-max-preview` after entitlement confirmation |

Only model IDs changed. V8 prompts and their hashes, contracts, exact schema
diagnostics, schema-derived enum repair, one-repair cap, revision limits,
budgets, and fail-closed behavior are unchanged. Executor alone retains
`max_tokens=4096`.

The temporary status and Reviewer revert target are machine-checked in
`agent_roles.v9.json`. Reverting later means selecting the v8 mapping again, or
creating the next configuration version with Reviewer set to
`qwen3.8-max-preview`; no prompt or workflow edit is required.

Offline checks:

```powershell
uv run pytest tests/test_three_role_runner_v9.py -q
uv run python scripts/run_three_role_smoke_v9.py --profile baseline
```

One intentional paid baseline after offline checks:

```powershell
$env:LLM_TIMEOUT_SECONDS = "120"
uv run python scripts/run_three_role_smoke_v9.py --real --profile baseline
```
