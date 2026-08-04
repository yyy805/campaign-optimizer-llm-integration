# Local three-agent workflow v6: Executor alignment

V6 is a narrow diagnostic/alignment release over the reviewed v5 trust
boundary. It does not change routing, Reviewer authority, the one-repair cap,
revision profiles, provider reservations, or fail-closed behavior.

The Executor prompt is now `executor_v3.md`, pinned by SHA-256 in
`agent_roles.v6.json`. It makes existing output-contract obligations explicit:
exact version/retry copying, exact claim/source/value binding, used-ID equality,
complete limitation claims, and exact numeric grounding. No new model authority
was introduced.

Rejected Executor output is never logged or echoed into the repair request.
The audit exposes only an allowlisted category and stable structural path:

| Category | Meaning | Stable path examples |
| --- | --- | --- |
| `JSON` | Response was not one JSON object | `output` |
| `GUARD` | Backend-owned status/retry/fallback state was wrong | `output.retry_count` |
| `SCHEMA` | JSON shape/types violated the output schema | `output.schema` |
| `SOURCE_BINDING` | A claim/reference/value did not bind to trusted context | `output.claims` |
| `EXCHANGE` | Candidate versions/intent or final exchange invariants failed | `output.exchange` |

The single bounded repair receives only the category, path, and a fixed
instruction. Candidate values, raw output, prompts, exception prose, API keys,
and workspace IDs are absent from serialized audit/results.

Offline check (no API call):

```powershell
uv run pytest tests/test_three_role_runner_v6.py -q
uv run python scripts/run_three_role_smoke_v6.py --profile baseline
```

Only after both offline commands pass, run one intentional paid smoke in the
same PowerShell where credentials were set:

```powershell
$env:LLM_TIMEOUT_SECONDS = "120"
uv run python scripts/run_three_role_smoke_v6.py --real --profile baseline
```

If it falls back, share only `status`, `fallback_reason`, and the `calls` array.
The category/path identifies the next contract adjustment without exposing the
candidate response.
