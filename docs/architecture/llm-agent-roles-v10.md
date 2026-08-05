# Local three-agent workflow v10: strict Reviewer output

The first real v9 baseline proved that Executor passed and Reviewer
`qwen3.7-plus` was reachable. Reviewer selected `PASS` but added top-level
`audit` and `corrections`; the v3 decision schema correctly rejected those
fields. The raw JSON Schema exception then escaped the runner, so v10 addresses
both output discipline and exception containment.

Reviewer v4 permits exactly seven top-level keys:

`schema_version`, `candidate_id`, `packet_digest`, `decision`,
`violation_codes`, `evidence_source_ids`, and `revision_actions`.

It explicitly forbids audit, corrections, reasoning, explanations and all other
keys, and states the PASS/REVISE/REJECT array rules from the existing schema.
The prompt is pinned by hash in `agent_roles.v10.json`.

Reviewer schema failures now produce only a safe category, validator and
sanitized structural path. Candidate-controlled extra property names become
`*`; raw values, schema dumps, exception prose, prompts and credentials are not
serialized. Invalid PASS is never cleaned up or accepted. It immediately
returns fixed fallback, with no Reviewer repair call and no change to provider
reservations.

V8/v9 Executor behavior remains unchanged: one bounded Executor repair,
Executor-only `max_tokens=4096`, trusted Reviewer packets, revision caps and
fail-closed publication.

Offline checks:

```powershell
uv run pytest tests/test_three_role_runner_v10.py -q
uv run python scripts/run_three_role_smoke_v10.py --profile baseline
```

After offline checks, run one intentional paid baseline:

```powershell
$env:LLM_TIMEOUT_SECONDS = "120"
uv run python scripts/run_three_role_smoke_v10.py --real --profile baseline
```
