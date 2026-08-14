---
title: 实施记录 — LLM Integration（逐 story，as-built）
author: Amelia（实施）
mode: as-built retrospective
status: final
created: 2026-08-14
binds: [epics.md 18 stories]
sources: [git log, tests/, scripts/, docs/]
---

# 实施记录（as-built）

> 每 story：实现摘要（白话）→ 关键代码路径 → AC 验证 → 取舍/注记 → 状态。路径均相对仓库根。

## Epic 1 可信解释底座

### Story 1.1 契约模板与校验基线 — done
- **实现**：五类输出各一份"填空模板"（JSON Schema），配套校验代码；越框即拒。
- **路径**：`campaign_optimizer/schemas/`（11 份 schema，FR1 信封为其中五份）、`campaign_optimizer/contracts/validation.py`、`exchange.py`。
- **AC 验证**：`tests/test_llm_contract_schemas.py`、`test_llm_contract_negative_matrix.py`、`test_llm_contract_edge_cases.py`、`test_llm_contract_status_matrix.py`。
- **注记**：契约主体 Draft-07，决策信封 Draft 2020-12（架构审查 round-1 M-1 修订口径）。

### Story 1.2 发布身份防篡改 — done
- **实现**：六字段发布身份 + 包裹校验和；任一篡改在构造 provider 前失败且不回显伪造值；资产漂移即 PackageDriftError。
- **路径**：`campaign_optimizer/llm/release_pin.py`（IDENTITY_FIELDS、load_verified_manifests）、`contracts/authority` 校验链。
- **AC 验证**：`tests/test_llm_convergence_integration.py`（六字段参数化）、`tests/test_ontology_release_pin.py`。证据 38fcc6d。
- **注记**：校验根 = 冻结 bundle（AD-3）；工作区根校验当前红（O-6，待合并复验），非运行时门禁。

### Story 1.3 待审核规则与 verdict 矛盾 fail-closed — done
- **实现**：status≠ACTIVE 的规则检索即拒（INACTIVE_RULE）；候选与已提交 verdict 矛盾时 packet 构造 fail-closed 且不回显。
- **路径**：`llm/retriever.py`、`llm/agent_workflow_v5.py`（ReviewerPacket.from_validated_exchange）。
- **AC 验证**：convergence 测试 `test_pending_r5_cannot_be_retrieved_as_decisive_llm_evidence`、`test_candidate_contradicting_committed_review_verdict_fails_closed_without_echo`。证据 38fcc6d。

### Story 1.4 路由硬门禁与意图白名单 — done
- **实现**：硬路由先判（复合/超模板零调用拒答）；无锚定才交 Triage；弃权/越界安全回退；50 题路由集离线守护。
- **路径**：`llm/intent_policy.py`（HybridIntentPolicy）、`tests/fixtures/llm_eval/v1/routing-safety.json`。
- **AC 验证**：`tests/test_llm_eval_v1_datasets.py`；活探针 run-records R4（REFUSED 0 调用 / ABSTAIN / NOT_ALLOWED）。

## Epic 2 三角色协作解释生成

### Story 2.1 Executor 起草与 OutputGuard — done
- **实现**：草稿过交换契约（限制完整披露、数值接地）；首轮类型错由内置修复循环消化（设计内，非缺陷）。
- **路径**：`llm/output_guard.py`、`contracts/exchange.py`、`llm/three_role_runner*.py` 修复循环。
- **AC 验证**：`tests/test_llm_workflow_exchange.py`、`test_llm_contract_runtime.py`；活跑 run-records R5（executor×2+reviewer×1，status OK）。证据 de07eb4。

### Story 2.2 Reviewer Function Calling 通道 — done
- **实现**：content 必须为空、工具调用恰一次、参数过本地权威 schema；binding 校验 digest/allowlist/动作语义，本地高于 provider。
- **路径**：`llm/three_role_runner_v13.py`（_call_reviewer）、`llm/reviewer_binding_v13.py`、`llm/tools/submit_reviewer_decision_v1.schema.json`。
- **AC 验证**：`tests/test_reviewer_v13.py`、`test_qwen_function_client_v12.py`；convergence 伪造桩（ForgedToolClient）。

### Story 2.3 Triage 介入与预算账本 — done
- **实现**：chat 模式且硬路由无锚定才介入；每候选每角色 ≤2、triage ≤1、总量账本封顶，超限 BUDGET_DENIED。
- **路径**：`llm/agent_workflow_v12.py`（BudgetLedgerV12、max_provider_calls_v12）、`intent_policy.py`。
- **AC 验证**：`tests/test_three_role_runner_v12.py`；convergence dry 预算兼容参数化；活探针 R4。

### Story 2.4 提示词版本盖章 — done
- **实现**：提示词/工具 schema 哈希钉死在角色配置；改一字加载即 ValueError；v6→v9 谱系逐版继承不原地改。
- **路径**：`llm/agent_workflow_v12.py`（load_role_configuration）、`llm/agent_roles.v15.json`、`llm/prompts/`。
- **AC 验证**：`tests/test_reviewer_judgment_dataset.py`（v13/v14/v15 配置钉死参数化）。

### Story 2.5 本地优先编排线 — done
- **实现**：orchestrator 服务端 chat 门禁 + 审计元数据；SessionStore 租户/用户级会话隔离（AD-11 显式豁免：进程内、非持久、demo 级）。
- **路径**：`llm/orchestrator.py`、`llm/session_store.py`。
- **AC 验证**：`tests/test_local_llm_orchestrator.py`。证据 80fe8f6/eeca02a。
- **注记**：demo 主路径不依赖编排线；门禁复用 HybridIntentPolicy，与主管道等价。

## Epic 3 可测量的 Reviewer 判断

### Story 3.1 冻结考卷与守护测试 — done
- **实现**：8 案例三类标签+安全类，钉住 canonical R5@2.0 发布身份；验证器拒六种变异；每案例期望 decision 合约可达。
- **路径**：`tests/fixtures/llm_eval/reviewer_judgment_v1/`（cases.json、validator.py、candidates/、README）。
- **AC 验证**：`tests/test_reviewer_judgment_dataset.py`。证据 c971b0b。

### Story 3.2 安全诊断与评测脚本 — done
- **实现**：默认 dry 零调用只报计划；--real 显式开启；行级诊断仅含违规码与动作形状（operation/source/target），不含 answer 原文。
- **路径**：`scripts/run_reviewer_judgment_eval_v14.py`。
- **AC 验证**：同文件 dry 子进程断言；`test_v14_rows_carry_safe_decision_diagnostics`。证据 4fee4e3。

### Story 3.3 验收运行与关闭 — done
- **实现**：v9（校准示例锚定边界）+ v15 配置；验收门"每题至少两次正确判断"。
- **AC 验证**：run-records R1–R3（8/8；7/7+1 NETWORK 非判断；补证 1/1；零判断错误）。证据 54c2e4e + 运行记录。
- **注记**：NETWORK 为代理抖动（0 tokens），不计判断错误；causal 案例 acceptable 集合修订带 README 记录（00cb04b）。

## Epic 4 权威知识与补充阅览室

### Story 4.1 确定性导出与忠实投影 — done
- **实现**：规则卡原文+元数据头导出，一字不改；双跑字节一致；ACTIVE/PENDING/RETIRED 显式。
- **路径**：`scripts/export_knowledge_base_v1.py`、`kb_export/v1/`。
- **AC 验证**：`tests/test_kb_export_v1.py`（确定性、忠实、状态显式）；denied-markers 校验在 `tests/fixtures/llm_eval/reviewer_judgment_v1/validator.py`，经 `tests/test_reviewer_judgment_dataset.py` 演习。证据 40002ba。

### Story 4.2 知识库发布与检索验收 — done
- **实现**：百炼库上传 7 文档；五硬门验收；阈值 0.20→0.60 调优后全过。
- **AC 验证**：控制台五门 @0.60（查 R5 只返回 R5；PENDING/RETIRED surfaced；限制命中；探针无结果）。台账 docs/knowledge-base-publications.md。
- **注记**：阈值已机器化进导出 manifest（a9f1513），重发布校验器强制读取。

### Story 4.3 发布台账与重发布流程 — done
- **实现**：KB ID ↔ checksum ↔ 阈值成行入台账；R5 转正重发布流程入交接文档。
- **路径**：`docs/knowledge-base-publications.md`、`docs/handoff-ontology-team-2026-08-06.md`。
- **AC 验证**：台账行 + a9f1513 阈值断言测试。

## Epic 5 演示与交接

### Story 5.1 演示网页（组 A） — done
- **实现**：侧边栏显示发布身份/提示词版本/凭据存在性（永不显示值）；dry-run 默认；失败只显示安全类别。
- **路径**：`app.py`。
- **AC 验证**：`tests/test_streamlit_ui.py`（渲染级；异常分支未演习，见测试设计 §9）。证据 19d30f0。

### Story 5.2 ECS 直接部署配方（组 B，不用 Docker） — done（首跑待验 O-7）
- **实现**：交接文档部署清单改为直跑 runbook（clone → uv sync --frozen → systemd → nginx → 安全组 8501 → 冒烟断言 bundle 根）；Dockerfile 保留为备选未验证。
- **路径**：`docs/handoff-ontology-team-2026-08-06.md` §4；`Dockerfile`（备选）。
- **AC 验证**：文档级；首跑待 ECS（O-7，首跑即首验）。

### Story 5.3 交接文档与全量推送（组 B） — done
- **实现**：交接三要素（架构不变式/部署清单/R5 重发布流程）；全部提交推送、tracked 树干净。
- **AC 验证**：git 校验（459c97f/f585272 及后续推送）。证据 2724f02。

## 汇总

18/18 story 状态 done；其中 5.2 含待外部条件子项（O-7 首跑），1.2 含 O-6 待合并复验注记。无 story 引入前向依赖；全部 AC 有测试或活跑/台账证据，除 5.2 首跑与 5.1 异常分支（均如实标注）。
