# V12 Reviewer Function Calling experiment

V12 forces one non-executable function, `submit_reviewer_decision_v1`. The
backend never runs it and never sends a tool result. Messages are only system
and user. Provider parameters force the function, disable parallel calls and
streaming, and do not set `max_tokens` (avoiding structured-output truncation).

The HTTP parser requires one choice, an allowed `finish_reason` (`tool_calls` or
`stop`), and complete function-call shapes with `type=function`. Missing or null
`tool_calls` normalize to zero calls: a valid provider envelope but a model
structure failure eligible for one safe retry. Multiple tools, wrong function,
normal text, non-JSON arguments and argument Schema errors follow the same model
structure policy. Malformed choices/call shapes, wrong call type, oversized
512-KiB HTTP responses or 64-KiB arguments are protocol failures with no retry.

The provider parameter Schema is a pinned compatibility subset. The complete
local Reviewer v3 Schema plus candidate/digest/source/action binding remains the
authority. Semantic or binding failures never retry. Retry uses the same packet,
model, prompt and tool Schema and adds only an allowlisted category.

One ledger enforces Executor <=2 and Reviewer <=2 per candidate. The total is
`4*(N+1)`, plus one for Triage. Baseline reserves four; an actual ambiguous-chat
dry path reserves five and includes Triage in the call ledger.

The isolated pilot has five cases (PASS 2, REVISE 2, REJECT 1), calls Reviewer
only, caps calls at ten, and reports per-case expected match, attempts, safe
failure category/code, latency and tokens plus aggregates. Acceptance requires
all decisions match and zero structure/safety failures; failure exits nonzero.
This is only a Function Calling compatibility pilot and does not establish
overall Reviewer quality.

```powershell
uv run pytest tests/test_three_role_runner_v12.py tests/test_qwen_function_client_v12.py -q
uv run python scripts/run_three_role_smoke_v12.py --profile baseline
uv run python scripts/run_three_role_smoke_v12.py --profile baseline --question "vague request"
uv run python scripts/run_reviewer_function_pilot_v12.py
```
