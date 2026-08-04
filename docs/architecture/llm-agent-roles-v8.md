# Local three-agent workflow v8: claim type alignment

The paid v7 baseline returned parseable JSON twice, and both candidates failed
at `EXECUTOR_SCHEMA.enum:output.claims[0].claim_type`. Reviewer was therefore
correctly not called. V8 changes only the Executor prompt and enum-repair
guidance; older versions and the trust boundary remain unchanged.

Executor v4 explicitly permits only five claim types and binds them to existing
sources:

| Claim type | Source |
| --- | --- |
| `PLAN_FIELD` | A normal field on a `plan_item_*` |
| `PLAN_PERIOD_FIELD` | `type`, `start_date`, or `end_date` from plan period, associated with a `plan_item_*` |
| `REVIEW_FIELD` | A field on a `review_item_*` |
| `FACT_VALUE` | A field on a `decision_fact_*` or `review_fact_*` |
| `RULE_FIELD` | A field on an allowed rule ID |

For this exact `claim_type` enum failure, the one bounded repair may receive
`allowed_values`. The values are read from the checked-in output schema and must
exactly match the five-code application allowlist. The rejected value, raw
candidate, exception prose, and credentials are never included. Other enum
locations and non-enum failures do not receive this list.

Offline checks:

```powershell
uv run pytest tests/test_three_role_runner_v8.py -q
uv run python scripts/run_three_role_smoke_v8.py --profile baseline
```

After those pass, one intentional paid baseline can be run in the credentialed
PowerShell:

```powershell
$env:LLM_TIMEOUT_SECONDS = "120"
uv run python scripts/run_three_role_smoke_v8.py --real --profile baseline
```
