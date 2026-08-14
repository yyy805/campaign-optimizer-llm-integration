---
title: 测试设计 — LLM Integration（Campaign Optimizer 解释员）
author: Murat（测试架构师）
mode: as-built retrospective
status: final
created: 2026-08-14
binds: [FR1-FR28, NFR1-NFR6, AD-1-AD-11]
sources: [PRD(final), epics.md, ARCHITECTURE-SPINE(final)]
---

# 测试设计（as-built）

## 1. 目标与范围

为"概率组件套在确定性门禁里"的系统提供三层测试策略与 FR→测试→证据 的完整追溯。范围覆盖 FR1–28；不含：运行可观测性（非目标）、ECS 首跑（O-7，待外部条件）、知识库 API 运行时接入（未引入）。

## 2. 三层策略

| 层 | 名称 | 成本 | 技术 | 证明什么 |
|---|---|---|---|---|
| L1 | 离线守护（tests/ 目录 570 通过/1 跳过；根跑另含 `_bmad-output` 下 17 个历史工件测试，不计入交付口径） | 零 | 契约/负例矩阵/变异/确定性/零 provider 桩 | 门禁抓得住错、离线不联网、导出确定 |
| L2 | 彩排（dry-run） | 零 | 子进程跑脚本默认模式，断言计划与零调用 | 脚本接线、预算数学、配置加载 |
| L3 | 真实验收 | 预算封顶、逐轮批准 | 冻结考卷/路由活探针/检索硬门 | 模型判断、路由行为、检索 fidelity |

**自证监考原则**：每类门禁都配"故意做错"用例（变异/负例/伪造桩），证明监考抓得住错；零 provider 桩（一旦试图联网即爆炸）证明"零消耗"是事实。

## 3. 风险优先级

- **P0（安全）**：FR2–5 防篡改/fail-closed、FR7 通道约束、FR15–16 路由、NFR1/NFR5——负例与对抗回归全配（test_llm_contract_adversarial_regressions、negative_matrix、convergence 六字段参数化、ForgedToolClient）。
- **P0（判断）**：FR11–14——冻结考卷 + 逐案例双重确认 + 安全诊断归因。
- **P1**：FR1/6/8/9/10/17–21——契约、管道、预算、检索。
- **P2**：FR22–28——UI、部署、编排线、交接（文档级验收 + AppTest + 首跑待验）。

## 4. 测试类型与技术

| 类型 | 代表文件 | 要点 |
|---|---|---|
| 契约/Schema | test_llm_contract_schemas / negative_matrix / edge_cases / status_matrix | 有效过、无效拒；六类负例 |
| 对抗回归 | test_llm_contract_adversarial_regressions / convergence 伪造桩 | 伪造身份/verdict/工具字段 → 拒且不回显 |
| 确定性 | test_kb_export_v1（双跑字节一致） | 导出可复现 |
| 零 provider 桩 | convergence（ProviderMustNotBeConstructed）、e2e dry | 离线不联网 |
| 子进程彩排 | test_reviewer_judgment_dataset / test_three_role_e2e_v15 的 dry 断言 | 脚本默认零调用 |
| UI | test_streamlit_ui（AppTest） | 渲染 + dry-run 安全 |
| 真实验收 | run_reviewer_judgment_eval_v14 --real、run_three_role_e2e_v15 --real | 预算封顶、诊断字段 |
| 检索硬门 | 控制台 @0.60 五门 | 查 R5 只返回 R5 等 |
| 路由活探针 | run-records R4 | REFUSED/ABSTAIN/NOT_ALLOWED |

## 5. 追溯矩阵（FR → 故事 → 测试 → 证据）

| FR | 故事 | 测试 | 证据 |
|---|---|---|---|
| FR1 | 1.1 | test_llm_contract_schemas / negative_matrix / edge_cases | 契约谱系提交 |
| FR2 | 1.2 | test_llm_convergence_integration（六字段参数化）；test_ontology_release_pin | 38fcc6d |
| FR3 | 1.2 | test_ontology_release_pin（drift 即 PackageDriftError） | 38fcc6d |
| FR4 | 1.3 | test_llm_convergence_integration（INACTIVE_RULE）；test_local_rule_retriever | 38fcc6d |
| FR5 | 1.3 | test_llm_convergence_integration（verdict 矛盾 fail-closed） | 38fcc6d |
| FR6 | 2.1 | test_llm_workflow_exchange；test_llm_contract_runtime；run-records R5 | de07eb4 |
| FR7 | 2.2 | test_reviewer_v13；test_qwen_function_client_v12 | v13 谱系 |
| FR8 | 2.3 | test_three_role_runner_v12.py；test_reviewer_v13.py；convergence 门禁；run-records R4 | 活探针 |
| FR9 | 2.3 | test_three_role_runner_v12（预算）；convergence（dry 预算兼容） | 谱系 |
| FR10 | 2.4 | test_reviewer_judgment_dataset（配置钉死参数化）；loader 校验 | agent_roles.v15 |
| FR11 | 3.1 | test_reviewer_judgment_dataset（验证器+六种变异） | c971b0b |
| FR12 | 3.1 | test_reviewer_judgment_dataset（pending 链绑定） | c971b0b |
| FR13 | 3.2 | test_reviewer_judgment_dataset（诊断字段不含原文） | 4fee4e3 |
| FR14 | 3.3 | run-records R1–R3 | 54c2e4e + 运行记录 |
| FR15 | 1.4 | test_llm_eval_v1_datasets（routing-safety 50）；run-records R4 | 活探针 REFUSED |
| FR16 | 1.4 | run-records R4；routing 数据集 | 活探针 NOT_ALLOWED |
| FR17 | 1.4 | test_llm_eval_v1_datasets | 谱系 |
| FR18 | 4.1 | test_kb_export_v1；test_local_rule_retriever | 40002ba |
| FR19 | 4.1 | test_kb_export_v1（忠实投影+确定性） | 40002ba |
| FR20 | 4.2 | 控制台五门 @0.60（截图未入库，召回百分比入台账） | 台账 |
| FR21 | 4.3 | test_kb_export_v1（阈值断言）；台账 | a9f1513 |
| FR22 | 5.1 | test_streamlit_ui（渲染级覆盖） | 19d30f0 |
| FR23 | 5.1 | test_streamlit_ui（dry 默认） | 19d30f0 |
| FR24 | 5.1 | test_streamlit_ui（渲染级；异常分支未演习，见 §9） | 19d30f0 |
| FR25 | 5.2 | 交接文档部署清单；**首跑待验（O-7）** | handoff |
| FR26 | 5.3 | 文档级验收（三要素核对） | 2724f02 |
| FR27 | 5.3 | git 校验（推送+tracked 干净） | 459c97f/f585272 |
| FR28 | 2.5 | test_local_llm_orchestrator | 80fe8f6/eeca02a |

NFR 横切：NFR1→AD-2 全负例；NFR2→预算测试+逐轮批准记录（批准记录为会话工件，非仓库工件）；NFR3→钉死参数化；NFR4→双跑一致；NFR5→denied-markers+不回显断言；NFR6→交接文档+本设计。

## 6. 测试数据管理

- 冻结件：考卷 8 / 路由 50 / 检索 12 / plan_a 夹具；修订须走带文档的 amendment（README Amendments 节）。
- 敏感面：denied-markers 入验证器；诊断字段仅结构 ID；运行记录为脱敏重构版并标注来源。

## 7. 环境与执行约定

- 本地：`uv run python -m pytest`（启动器被应用控制策略拦截，勿用 `uv run pytest`）。
- 真实跑：用户在自有终端设 DASHSCOPE_API_KEY/DASHSCOPE_WORKSPACE_ID 后 `--real`；全 CONFIG/0 tokens = 缺变量，非回归。
- push 走代理；代理未启报连接错，非仓库问题。

## 8. 出口标准（已执行）

- L1：tests/ 目录 570 通过/1 跳过（根跑口径 587/588 含 `_bmad-output` 历史工件测试 17 个，PRD 同口径处以此限定为准）；L2 各脚本 dry 断言过；L3：考卷 8/8+7/7+1 补证零判断错误、路由三层活探针全 fail-closed、检索五门 @0.60 全过。

## 9. 已知缺口（如实）

- Triage 活路由质量：仅验证 fail-closed 行为，未验证分类准确率（flash 误路由已知，UI 主路径兜底）。
- FR25 首跑待 ECS（O-7）；KB API 未入运行时（引入前须新 AD）；可观测性非目标。
- O-6：main 工作区对发布清单的根级校验当前抛 PackageDriftError（唯一已知活着的失败校验）；运行时经 bundle 根安全（AD-3），待 canonical/Hannah 合并后复验至 MANIFEST_OK。
- FR24 异常分支（运行失败只显示安全类别）未被测试演习，仅渲染级覆盖。
