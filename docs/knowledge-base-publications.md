# Knowledge Base Publications

Maps Bailian knowledge base publications to the pinned ontology release
identity recorded in `kb_export/v1/manifest.json`. Append one row per
publication; never edit historical rows.

| Date | Bailian KB ID | Region / Workspace | Export suite | package_checksum | rule_version | Notes |
|---|---|---|---|---|---|---|
| 2026-08-06 | eeirxr7djz | cn-beijing / LLMWorkspace07 | kb-export-v1 | 626cfbdedc954f41c7a335cf6178886c8c5cc3c71b2c1b7400ed6f87595d3513 | R5@2.0-campaign-pending | 7 docs R1–R7; default 600-char chunking (header becomes its own chunk); text-embedding-v4; platform storage; qwen3-rerank QA mode; TopK 50/50; final recall 5; **similarity threshold 0.60** |

## Retrieval acceptance (S3)

Hard gates from `tests/fixtures/kb_retrieval_v1/questions.json`:

- "查询R5" returns R5 only (no cross-rule contamination);
- R5 hits surface PENDING_HUMAN_REVIEW; R7 hits surface RETIRED;
- out-of-scope probe returns no result instead of fabricated content.

Accepted 2026-08-06 at threshold 0.60: all five gates pass (R5-only 3 chunks 74/72/69%; R5 status 84%; R7 RETIRED 78/71%; limitations chunk first at 69%; probe returns "未检索到数据"). At the default threshold 0.20 the contamination and probe gates FAIL (43–54% noise chunks) — the 0.60 threshold is part of the publication configuration and must travel with any re-publication.
