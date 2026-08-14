# 审查报告 — IMPLEMENTATION-RECORDS.md（Round 1）

- 审查对象：`D:\AAA Data science and AI for busines\AI-projects\bmad\_bmad-output\planning-artifacts\implementation-records-2026-08-14\IMPLEMENTATION-RECORDS.md`
- 审查日期：2026-08-14
- 模式：只读对抗式审查（未改被审文档）
- 核对输入：仓库代码 `campaign_optimizer/`、`scripts/`、`tests/`、`docs/`、`app.py`、`kb_export/`，`git log`（400 条），`_bmad-output/planning-artifacts/epics.md`，PRD（`prds/prd-ai-workflow-lab-2026-08-06/prd.md` 及两轮 review），TEST-DESIGN §9，ARCHITECTURE-SPINE（含 Deferred）

## Verdict

**PASS（高置信）** — 18/18 story 的路径、函数名、测试名、commit 哈希、run-records 数字、开放项注记全部现场核验属实；未发现幽灵路径或虚构证据。存在 1 处 low 级证据归属错误（denied-markers 归错测试文件）与 3 条 info 级备注。

## Severity 计数

| 级别 | 数量 |
|---|---|
| high | 0 |
| medium | 0 |
| low | 1 |
| info | 3 |

## 核验方法与统计

1. **路径真实性（要求 ≥12，实查 30+）**：逐 story 核验，全部命中——
   - 目录/文件：`campaign_optimizer/schemas/`（11 份 schema，FR1 五信封 llm_request/llm_context/llm_workflow_output/ontology_review/final_plan 全在）、`contracts/validation.py|exchange.py|authority.py`、`llm/release_pin.py`、`llm/retriever.py`、`llm/agent_workflow_v5.py`、`llm/intent_policy.py`、`llm/output_guard.py`、`llm/three_role_runner_v13.py`、`llm/reviewer_binding_v13.py`、`llm/tools/submit_reviewer_decision_v1.schema.json`、`llm/agent_workflow_v12.py`、`llm/agent_roles.v5–v15.json`（谱系逐版在）、`llm/prompts/`（含 reviewer_v9.md）、`llm/orchestrator.py`、`llm/session_store.py`、`scripts/run_reviewer_judgment_eval_v14.py`、`scripts/export_knowledge_base_v1.py`、`kb_export/v1/`（R1–R7 + manifest + README）、`tests/fixtures/llm_eval/v1/routing-safety.json`、`tests/fixtures/llm_eval/reviewer_judgment_v1/`（cases.json/validator.py/candidates/README）、`tests/fixtures/llm_eval/runs/run-records-2026-08-06.md`、`tests/fixtures/kb_retrieval_v1/questions.json`、`docs/knowledge-base-publications.md`、`docs/handoff-ontology-team-2026-08-06.md`、`app.py`、`Dockerfile`、16 个被引用的 test_*.py 文件全部存在。
   - 函数/类：`load_role_configuration`（agent_workflow_v12.py:34）、`BudgetLedgerV12`（:85）、`max_provider_calls_v12`（:75）、`IDENTITY_FIELDS`（release_pin.py:16，恰为 epics 1.2 点名的六字段）、`load_verified_manifests`（:67）、`PackageDriftError`（ontology/publication.py:22）、`ReviewerPacket.from_validated_exchange`（agent_workflow_v5.py:88/97）、`HybridIntentPolicy`（intent_policy.py:32）、`_call_reviewer`（three_role_runner_v13.py:19）、`ForgedToolClient`（test_llm_convergence_integration.py:51）、`SessionStore/OrchestrationResult/AttemptMetadata`、`validate_answer_numeric_grounding`（exchange.py:41）。
   - 测试函数：`test_pending_r5_cannot_be_retrieved_as_decisive_llm_evidence`、`test_candidate_contradicting_committed_review_verdict_fails_closed_without_echo`、`test_v14_rows_carry_safe_decision_diagnostics`、`test_v11_v12_v13_dry_run_budget_compatibility`、`test_role_configuration_pins_reviewer_prompt_and_dry_run_stays_zero_provider`（v13/v14/v15 参数化属实）、`test_validator_rejects_key_mutations`（六种变异 count/duplicate_id/broken_candidate_ref/rule_field_claim/verdict_tamper/label_mismatch 与 epics 3.1 逐一对应）、`test_orchestrator_reads_only_server_history_and_isolates_tenants`。
   - 数量断言：routing-safety.json 实数 50 条 ✓；cases.json 实数 8 案例、三类 decision（PASS/REVISE/REJECT）✓；kb_export/v1 七张规则卡 ✓。
2. **Commit 真实性（要求 ≥6，实查 15/15）**：38fcc6d、de07eb4、80fe8f6、eeca02a、c971b0b、4fee4e3、54c2e4e、00cb04b、40002ba、a9f1513、19d30f0、2724f02、459c97f、f585272、5c4e6d5 全部在 `git log` 中，且提交信息与记录语境吻合（如 a9f1513 = threshold 0.6 入 manifest、00cb04b = causal acceptable codes 修订）。
3. **run-records 核对**：R1 8/8 ✓、R2 7/7+1 NETWORK（0 tokens、代理抖动、非判断错误）✓、R3 补证 1/1 ✓、R4 REFUSED 0 调用/TRIAGE_ABSTAIN/TRIAGE_INTENT_NOT_ALLOWED ✓、R5 executor×2+reviewer×1 OK（首轮 limitations_included 类型错被内置修复）✓ —— 与 story 1.4/2.1/2.3/3.3 的引用逐字一致。
4. **AC 一致性**：18 story 的 AC 验证行与 epics.md 对应 AC 全部吻合；限定声明属实——5.2"首跑待验 O-7"与 PRD §9 O-7、脊 Deferred"ECS 首跑验证（首跑即首验）"一致；5.1"异常分支未演习"与 TEST-DESIGN §9 第 103 行逐字对应；汇总"除 5.2 首跑与 5.1 异常分支外全部 AC 有证据"成立。
5. **注记诚实性**：O-6 注记与 PRD O-6、脊 Rule（line 59"工作区根校验…不作运行时门禁"）、Deferred"canonical/Hannah 合并排序"三方一致；NETWORK 注记与 run-records R2 一致；修复循环"设计内非缺陷"与 R5 行及 runner 修复路径一致；AD-11 豁免（进程内、非持久、demo 级）与脊 line 115 逐字一致；Draft-07/Draft 2020-12 口径与脊 line 127 一致；a9f1513 阈值机器化与 kb_export/v1/manifest.json `similarity_threshold: 0.6` + test_kb_export_v1.py:44 断言一致；台账行（KB ID eeirxr7djz ↔ checksum 626cfbde… ↔ 0.60）与 docs/knowledge-base-publications.md 一致；交接文档 §2 架构不变式/§4 ECS 直跑清单/R5 转正重发布流程（line 41）三要素齐 ✓。tracked 树干净（git status 仅 untracked，无 modified/staged）✓，本地无领先 origin/main 的提交 ✓。

## Findings

### F-01 [low] Story 4.1：denied-markers 证据归属错误
- **位置**：IMPLEMENTATION-RECORDS.md Story 4.1 AC 验证行。
- **问题**：记录称 `tests/test_kb_export_v1.py` 覆盖"（确定性、忠实、状态显式、denied-markers）"。该文件实际只有 5 个测试（deterministic / faithful_projection_with_explicit_status / manifest_pins… / pending_and_retired_statuses / question_set_frozen），**无任何 denied-markers 检查**。
- **证据**：DENIED_DATA_MARKERS 实际位于 `tests/fixtures/llm_eval/reviewer_judgment_v1/validator.py:53` 与 `tests/fixtures/llm_eval/v1/validator.py:14`，经 `tests/test_reviewer_judgment_dataset.py::test_validator_accepts_frozen_dataset`（VALIDATOR.validate_all）演习——属 Epic 3 考卷的守护，非 KB 导出测试。且 epics.md Story 4.1 的 AC 本身不含 denied-markers（该要求属 NFR5）。
- **定性**：证据归属错误（misattribution），非虚构——检查本身真实存在且被测试，只是不在所引文件里。
- **建议**：把括号改为"（确定性、忠实、状态显式）"；如需保留 denied-markers，改引 `tests/test_reviewer_judgment_dataset.py`（validator 演习）并注明其属 NFR5/考卷守护。

### F-02 [info] Story 1.1："脊 M-1" 标签口径松
- **位置**：Story 1.1 注记"契约主体 Draft-07，决策信封 Draft 2020-12（脊 M-1 修订口径）"。
- **问题**：M-1 不是脊内标记，而是 `architecture-ai-workflow-lab-2026-08-13/review-arch-round1.md` 的 finding 编号（M-1｜Stack 表愿望式概括）；脊本体（ARCHITECTURE-SPINE.md:127）承载的是修订后措辞。
- **证据**：review-arch-round1.md:60–63；ARCHITECTURE-SPINE.md:127。实质内容（9 份 draft-07 + reviewer/triage 两份 Draft 2020-12）属实。
- **建议**：改为"（架构审查 round-1 M-1 修订后脊口径）"或删去编号。

### F-03 [info] Story 5.3："全部提交推送"的验证边界
- **位置**：Story 5.3 AC 验证与汇总"tracked 树干净"。
- **问题/边界**：本次验证为本地口径：tracked 树干净 ✓、本地无领先 origin/main 的提交 ✓，但未 fetch（远端新鲜度不可证）。另：当前 untracked 集较 PRD round-2 F-07 清单新增了 `docs/pg-drill-runbook.md`、`docs/technical-specification-ontology.md`、`scripts/drill_*.py`×6（并行工作产物）——FR27 措辞限于 tracked 树，字面上仍成立，但若读者把"全部提交推送"理解为"工作区一切文件"，会被误导。
- **建议**：无需改记录本身；如后续复审，可在 5.3 注明"以 tracked 树为准；untracked 遗留见 PRD §9 口径"。

### F-04 [info] Story 5.2 未引用其对应实施 commit（非缺陷）
- **位置**：Story 5.2 实现行。
- **观察**：部署叙事改为 ECS 直跑的实际提交是 6c52a92（docs: switch deployment narrative to ECS direct run），记录未引用；但 epics 5.2 的证据锚点本就只要求 handoff 文档，记录未作虚假声明。仅列为完备性备注。

## 结论

被审记录整体可信：路径零幽灵、commit 15/15 命中、run-records 数字逐条吻合、开放项限定（O-6/O-7/NETWORK/异常分支）与 PRD/测试设计/架构脊三方互证一致。唯一 low 级问题（F-01）为证据归属错误，修正一行即可。
