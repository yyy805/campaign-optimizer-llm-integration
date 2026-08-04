# Three-role Qwen smoke

Run local preflight first. It validates v5 prompt hashes, request/context
contracts, routing rules, and conservative provider-call reservation. It does
not read credentials, construct a Qwen client, or make a network request.

```powershell
uv run python scripts/run_three_role_smoke.py --profile baseline
```

The local sequence is: hard-refusal rules, deterministic explicit-intent rules,
then Triage only for genuinely ambiguous chat text. For a real ambiguous chat,
backend Python seals the request to the validated Triage intent before Executor
sees it. Triage cannot select an intent outside the server allowlist.

For a deliberate paid smoke, use only the current PowerShell process. Do not put
either value in source files, Git, screenshots, or chat. The existing Beijing
client uses the workspace identifier to construct its endpoint; it is
configuration, not another API secret.

```powershell
$env:DASHSCOPE_API_KEY = Read-Host "DASHSCOPE_API_KEY" -MaskInput
$env:DASHSCOPE_WORKSPACE_ID = Read-Host "DASHSCOPE_WORKSPACE_ID"
uv run python scripts/run_three_role_smoke.py --real --profile baseline
Remove-Item Env:DASHSCOPE_API_KEY
Remove-Item Env:DASHSCOPE_WORKSPACE_ID
```

This dry-run command preflights and reserves the Triage branch; it does not
invoke any provider model. Terminal output contains audit metadata only, never
prompts, raw responses, answers, workspace identifiers, or credentials.

```powershell
uv run python scripts/run_three_role_smoke.py --question "vague request"
```
