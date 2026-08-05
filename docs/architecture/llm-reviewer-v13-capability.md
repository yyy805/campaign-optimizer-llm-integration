# Reviewer Function Calling v13 capability gate

Qwen3.7-plus is a hybrid-thinking model. In thinking mode, the compatible API does not support forcing one specific function through `tool_choice`. The v13 Reviewer request therefore fixes `enable_thinking: false` while retaining the v12 strict tool schema, forced function choice, parser, semantic gates, retry rules, and budget ledger.

Run `scripts/run_reviewer_capability_smoke_v13.py --real` first. It makes at most one Reviewer call and never retries. It reports only a safe category/code, HTTP status, request ID, latency, and token count; it never reports the HTTP body, exception text, API key, or workspace ID. Run the separate five-case v13 pilot manually only after this smoke returns `PASS`.
