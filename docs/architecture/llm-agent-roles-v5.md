# Local three-agent workflow (v5)

## Decision

The workflow is built locally as Python orchestration plus versioned prompts,
schemas and tests. Qwen inference uses the existing Bailian-compatible client;
three separate Bailian applications and API keys are not needed. A Bailian
workflow can later be used for a visual demo, not as this runtime's dependency.

| Role | Pinned model | When called | Output authority |
| --- | --- | --- | --- |
| TRIAGE | `qwen3.7-plus` | Only an ambiguous explanation request | Route one approved intent, or abstain |
| EXECUTOR | `qwen3.7-max` | Every server-created task/revision | Structured candidate only |
| REVIEWER | `qwen3.8-max-preview` | Every candidate, including the last | PASS, typed revision, or rejection |

These are Model Studio API model IDs, not the hyphenated aliases used by some
local tools. The runner sends the configured value to the Bailian client
unchanged. Access to `qwen3.8-max-preview` is workspace-dependent and is
therefore verified only by the later Reviewer live smoke test.

## Contract and integrity boundary

`agent_roles.v5.json` fixes every alias, prompt artifact and prompt SHA-256.
Startup rejects missing, duplicate or empty aliases; changed prompt bytes; or any
profile outside the approved `0/1/3/5` revision caps. TRIAGE v2 explicitly maps
ROUTE to a single intent and confidence ≥0.8, and ABSTAIN to null intent.
Reviewer v3 explicitly returns the v3 decision schema and copies the server
provided candidate ID and packet digest.

Python remains authoritative for routing eligibility, request version, contract
validation, allowed IDs, retry state, caps and fallback. It first validates the
existing request/plan/review/context/candidate exchange, then deep-copies a
strictly allowlisted Reviewer packet. The raw user question, history and raw
reviewer prose are excluded. The server hashes candidate ID, projected task,
trusted context, candidate output and the pinned Reviewer prompt hash into the
packet digest. A Reviewer decision must echo that digest exactly, preventing a
decision from being replayed against a different candidate or context.

## Revision and reservation policy

Every changed candidate is reviewed again. If the last permissible review is not
PASS, Python returns fallback—never an unreviewed candidate. With N permitted
revisions, role calls are `2 × (N + 1)` and conservative provider reservations
are `3 × (N + 1)`, allowing one bounded Executor content-repair call per
candidate.

| Cap N | Role calls | Provider reservation |
| ---: | ---: | ---: |
| 0 | 2 | 3 |
| 1 | 4 | 6 |
| 3 | 8 | 12 |
| 5 | 12 | 18 |

Run the offline contract gate (no API call or quota spent):

```powershell
& .\.venv\Scripts\python.exe -m pytest tests/test_llm_agent_workflow_v5.py -q
```
