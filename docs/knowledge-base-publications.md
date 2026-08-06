# Knowledge Base Publications

Maps Bailian knowledge base publications to the pinned ontology release
identity recorded in `kb_export/v1/manifest.json`. Append one row per
publication; never edit historical rows.

| Date | Bailian KB ID | Region / Workspace | Export suite | package_checksum | rule_version | Notes |
|---|---|---|---|---|---|---|
| 2026-08-06 | eeirxr7djz | cn-beijing / LLMWorkspace07 | kb-export-v1 | 626cfbdedc954f41c7a335cf6178886c8c5cc3c71b2c1b7400ed6f87595d3513 | R5@2.0-campaign-pending | 7 docs R1–R7; max chunk 6000 (one chunk per card); text-embedding-v4; platform storage |

## Retrieval acceptance (S3)

Hard gates from `tests/fixtures/kb_retrieval_v1/questions.json`:

- "查询R5" returns R5 only (no cross-rule contamination);
- R5 hits surface PENDING_HUMAN_REVIEW; R7 hits surface RETIRED;
- out-of-scope probe returns no result instead of fabricated content.

Record Top-K / threshold used at test time next to the results.
