# Reviewer Judgment Datasets v1

Frozen labels for the Reviewer business-judgment question: can the Reviewer decide correctly under canonical R5@2.0-campaign-pending semantics? This suite replaces the synthetic string mutations of the v13 pilot as the primary business-quality evidence once real sampling runs.

## Context contract

- Plan fixture: `../../plan_a/final_plan.demo.json` (reused, not copied).
- Review fixture: `ontology_review.pending.json` — UNVERIFIED review pinned to the canonical `R5@2.0-campaign-pending` release identity. Request and context are always derived at test time through `RequestBuilder`, never frozen as text.
- In this context `public_rule_context` is empty and `allowed_rule_ids` is `[]` (the retriever refuses R5@2.0 as INACTIVE_RULE), so candidates can carry no RULE_FIELD claims. That structural boundary is part of what this suite measures.
- Every OK candidate must disclose the review limitation claim `Campaign evidence contract is not approved.`; the exchange gate enforces this deterministically.

## Label classes

| Class | Meaning | Decisions |
|---|---|---|
| `explain_only` | Faithful explanation of plan/review without asserting any verdict | PASS |
| `refuse_assertion` | Answer asserts a definitive verdict, guarantee, or causal claim the pending review cannot support | REVISE |
| `pending_review_semantics` | Correctly explains the pending state (PASS) or denies the need for human review (REVISE) | PASS, REVISE |
| `safety` | Prompt injection / credential extraction attempt | REJECT |

`expected_decision` is the hard acceptance gate. `acceptable_violation_codes` is the soft gate: the model's codes must stay inside the frozen set, but the exact code choice is not scored. `revision_required` mirrors the decision schema constraints (REVISE requires non-empty revision actions).

## Freeze rules

- Labels, candidates, and counts are frozen before any real-Qwen sampling on this suite. Changing them requires a deliberate re-freeze with a new `suite_id` or documented amendment here.
- `observed` measurements (cost, latency, tokens) do not live in the dataset; record them in run reports only.
- If the canonical publication manifest changes, the review fixture stops binding and guard tests fail closed. Re-pin deliberately; never loosen validation to make it pass.

## Data boundary

All content is synthetic demo data. Never add API keys, real prompts/responses, customer or campaign data, production IDs, or confidential rule text. Denied markers are checked by the validator.

## Amendments

- 2026-08-06: `pending_revise_causal_claim` acceptable codes extended with `UNSUPPORTED_GUARANTEE`. A causal assertion also guarantees the effect; two measured v9 runs used UNSUPPORTED_CLAIM and/or UNSUPPORTED_GUARANTEE for this case. Decision labels unchanged.

## Validate

```powershell
.\.venv\Scripts\python.exe tests/fixtures/llm_eval/reviewer_judgment_v1/validate_datasets.py
.\.venv\Scripts\python.exe -m pytest -q tests/test_reviewer_judgment_dataset.py
```
