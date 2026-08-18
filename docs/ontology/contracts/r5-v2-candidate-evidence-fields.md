# R5 v2 Candidate Evidence Fields

Status: **non-authoritative proposal**

R5 remains `PENDING_HUMAN_REVIEW`. This document is outside the current ontology publication roots, the legacy runtime copy, and all immutable release bundles. It has no runtime effect and does not change the current R5 contract.

## Candidate field requests

The following names are requests for an upstream and cross-team contract. They are not accepted ontology fields:

| Candidate field | Intended evidence |
| --- | --- |
| `campaign_revenue_contribution_share` | Revenue contribution share resolved to Campaign for the governed report window. |
| `campaign_spend_share` | Spend share using the same Campaign population, window, advertiser, market, and currency. |
| `campaign_attribution_evidence_reliable` | Explicit reliability decision for Campaign-level attribution evidence. |
| `bridge_fallback_used` | Whether Campaign resolution used fallback or inferred mapping. |
| `evidence_batch_id` | Stable identifier joining attribution, spend, and review evidence from one batch. |
| `source_artifact_hash` | Integrity binding to the exact upstream evidence artifact. |
| `report_start_date` / `report_end_date` | Explicit governed report window carried into the evidence contract. |

## Decisions required before acceptance

- Define the governed touchpoint-to-Campaign bridge and the behavior for ambiguous or missing mappings.
- Define contribution and cost conservation checks across the bridge.
- Require one advertiser, market, report window, and currency for every comparison batch.
- Treat a zero denominator as no coverage rather than zero share.
- Define behavior for empty `official_share`, unreliable evidence, and `bridge_fallback_used=true`.
- Bind the evidence batch and artifact hash to the final plan review artifact.
- Calibrate and approve any R5 thresholds before enabling automated decisions.

After those decisions are approved, historical attribution may justify conflict-only review of a proposed budget increase. It must never independently support a budget decrease or a causal claim.

## Upstream tracking note

The algorithm repository file `modules/mta_strategy_recommendation/data/simulated/strategy_request.json` still contains these historical lineage strings:

- `mta_source.attribution_file`: `modules/amc_mta/outputs/attribution/amc_mta_recommended_attribution.csv` → `modules/mta_attribution/outputs/attribution/amc_mta_recommended_attribution.csv`
- `mta_source.entity_file`: `modules/amc_mta/data/simulated/amc_touchpoint_entity_aggregate_sample.csv` → `modules/mta_attribution/data/simulated/amc_touchpoint_entity_aggregate_sample.csv`

These corrections belong upstream; this repository does not mutate the algorithm team's data artifacts.
