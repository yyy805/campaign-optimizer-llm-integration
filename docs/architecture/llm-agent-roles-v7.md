# Local three-agent workflow v7: exact schema diagnostics

The paid v6 baseline proved that `qwen3.7-max` returned parseable JSON twice,
but both candidates failed `EXECUTOR_SCHEMA:output.schema`; Reviewer was never
called. V7 is a narrow diagnostic release that preserves the v5/v6 trust
boundary, role models, prompt bytes, one-repair cap, revision profiles, budget
reservations, and fail-closed behavior.

For JSON Schema failures, audit now records only:

- an allowlisted validator keyword such as `required`, `type`, `const`, `enum`,
  or `additionalProperties`; and
- a sanitized structural path such as `output.claims[0].source_id`.

Required-field names come from the schema, array indexes are numeric, and
unknown/additional property names are rendered as `*`. Instance values, raw
responses, exception messages, prompts, API keys, and workspace IDs are never
put into audit, result, or the bounded repair payload.

V7 also sets `max_tokens=4096` only for Executor calls. The existing contract
permits an 8,000-character answer plus structured claims, so 4,096 output tokens
is a conservative ceiling rather than a terse-answer target. Triage and Reviewer
retain their previous generation settings because their schemas are much
smaller and the paid failure concerned Executor only.

Offline checks:

```powershell
uv run pytest tests/test_three_role_runner_v7.py -q
uv run python scripts/run_three_role_smoke_v7.py --profile baseline
```

One intentional paid baseline, only after offline checks pass:

```powershell
$env:LLM_TIMEOUT_SECONDS = "120"
uv run python scripts/run_three_role_smoke_v7.py --real --profile baseline
```

If it falls back, share only `status`, `fallback_reason`, and `calls`. A code
such as `EXECUTOR_SCHEMA.required:output.claims[0].source_id` identifies the
structural defect without exposing the rejected candidate.
