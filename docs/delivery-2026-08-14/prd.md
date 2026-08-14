---
title: 大模型接入（Campaign Optimizer 解释员）PRD
status: final
created: 2026-08-06
updated: 2026-08-06
mode: as-built retrospective
---

# 大模型接入（Campaign Optimizer 解释员）PRD

> 回溯式 PRD：所有需求均映射已交付代码与真实验收证据（commit 哈希/测试数/验收记录）。技术实现细节见同目录 `addendum.md`。

## 1. 产品愿景与红线

**愿景**：为广告审核系统提供一个"AI 解释员"——把"投放方案 + 本体审核"翻译成人话，让非技术用户读懂审核结论与方案限制。

**红线（不可妥协）**：
- R-1 模型**只解释、不裁决**：不得自行出具 SUPPORT/CONFLICT/NOT_APPLICABLE 结论。
- R-2 未批准的内容必须说"还没批准"：`PENDING_HUMAN_REVIEW` 规则 fail-closed，不得作为决定性证据。
- R-3 任何输出过不了门禁就安全回退："无法回答"永远好过"胡说"。

## 2. 用户与利益相关方

| 角色 | 诉求 | 我们的承诺 |
|---|---|---|
| 演示观众/老师 | 看懂系统能力与边界 | 演示网页 + 如实披露已知限制 |
| 本体团队 | 规则权威不被篡改；接手上云 | 版本锁 + 交接文档 + 发布台账 |
| LLM 团队（我们） | 可控、可测、可交接 | 自有门禁代码 + 冻结考卷 + 预算纪律 |
| 未来运维 | 部署与排障有章可循 | Dockerfile + 交接文档 + 故障排除节 |

## 3. 成功指标与反指标

- M-1 Reviewer 业务判断：验收门为"**每题至少两次正确判断**"（实际：第一轮 8/8；确认轮 7/7 有效判断 + 1 次 NETWORK（0 tokens，非判断错误）；补证 1/1；累计零判断错误。见 `tests/fixtures/llm_eval/runs/run-records-2026-08-06.md`）。
- M-2 检索：5 硬门全过（查 R5 只返回 R5；R5 带 pending 标记；R7 带 RETIRED；限制命中；恶意提问无结果）。
- M-3 路由：三层安全行为真实验证全 fail-closed。
- M-4 离线回归：588 项收集（587 passed / 1 skipped），零真实调用。
- **反指标**：过度修订率——同一合规答案跨轮翻转即视为边界噪声，须用逐案例双重确认而非单轮度量（M-1 的逐案例门设计即为此）。

## 4. 范围与取舍

- **在范围内**：契约门禁、三角色管道、Reviewer 验收、路由安全、轻量知识库（补充通道）、本地优先编排线（并行交付）、演示网页、ECS 直接部署配方（不用 Docker）、交接与台账。
- **范围取舍（带理由）**：
  - UX 设计阶段跳过：UI 为功能性 demo（Streamlit 单页），无多角色体验设计需求。
  - 平台 Workflow 不采用：安全门禁必须放在自有代码才能 fail-closed（黑盒不可控）。
  - Triage 已知限制保留：flash 模型对评审类问句误路由，全部 fail-closed 兜底，UI 主路径不经过它（见 §9）。
  - 知识库定位为补充通道：权威内容走确定性投影（见 FR-18）。

## 5. 功能需求（FR，as-built）

### F1 契约与安全门禁
- FR-1 五份输出契约模板（请求/上下文/工作流输出/本体审核/最终方案），缺字段或错类型即拒收。证据：`campaign_optimizer/schemas/`、`contracts/`。
- FR-2 发布身份六字段（ontology_version/rule_version/engine_version/schema_version/source_commit/package_checksum）任一篡改，在构造 provider 前失败且不回显伪造值。证据：收敛门禁测试 6 项参数化。
- FR-3 发布包校验和验证：资产漂移即 `PackageDriftError`。证据：`release_pin.py` + `test_ontology_release_pin.py`。
- FR-4 待审核规则（status≠ACTIVE）不得作为决定性证据进入上下文（INACTIVE_RULE）。证据：`test_pending_r5_cannot_be_retrieved_as_decisive_llm_evidence`。
- FR-5 候选与已提交审核 verdict 矛盾时 fail-closed。证据：`test_candidate_contradicting_committed_review_verdict_fails_closed_without_echo`。

### F2 三角色管道
- FR-6 Executor 起草解释；输出必须过 OutputGuard 与交换契约（限制完整披露、数值接地）。证据：`output_guard.py`、`exchange.py`。
- FR-7 Reviewer 以 Function Calling 唯一通道提交决策（PASS/REVISE/REJECT），content 必须为空、工具调用恰一次、参数过本地权威 schema。证据：v13 runner + `reviewer_binding_v13.py`。
- FR-8 Triage 仅在 chat 模式且硬路由无锚定时介入；弃权即安全回退。证据：`intent_policy.py`、真实弃权记录。
- FR-9 预算账本：每候选每角色 ≤2 次、triage ≤1、总量封顶；超限 BUDGET_DENIED。证据：`BudgetLedgerV12`。
- FR-10 角色提示词与工具 schema 哈希钉死；改一字必须升版复验。证据：`agent_roles.v12–v15.json` + 加载器校验。

### F3 Reviewer 业务验收
- FR-11 冻结考卷 8 案例、三类标签（只能解释/必须拒绝断言/待审核语义）+ 安全类，decision match 为硬门。证据：`reviewer_judgment_v1`。
- FR-12 考卷钉住 canonical R5@2.0-campaign-pending 发布身份；manifest 漂移即失败。证据：review fixture + 守护测试。
- FR-13 行级安全诊断：记录模型引用的违规码与修订动作形状（operation/source/target），不含 answer 原文。证据：提交 4fee4e3。
- FR-14 验收门：每题至少两次正确判断（第一轮 8/8；确认轮 7/7 有效 + 1 NETWORK；补证 1/1）；单轮不足以为证。证据：入库运行记录（重构版，见 §7）。

### F4 路由安全
- FR-15 硬路由：复合/超模板解释问句 fail-closed 拒答（零调用）。证据：真实 REFUSED 记录。
- FR-16 意图白名单：triage 路由意图越界即 SYSTEM_FALLBACK。证据：真实 TRIAGE_INTENT_NOT_ALLOWED 记录。
- FR-17 50 题路由安全数据集离线守护。证据：`routing-safety.json`。

### F5 轻量知识库（补充通道）
- FR-18 混合架构不变式：权威上下文走 release-pin 确定性投影；检索结果一律不可信数据。证据：架构文档与导出脚本 docstring。
- FR-19 导出为忠实投影（元数据头 + 规则卡原文），ACTIVE/PENDING/RETIRED 状态显式。证据：`kb_export/v1/`。
- FR-20 检索硬门：查 R5 只返回 R5；pending/retired 标记必须 surfaced；越界提问无结果。证据：百炼库 eeirxr7djz @ 阈值 0.60 验收。
- FR-21 发布台账：每次发布记录 KB ID ↔ package_checksum；阈值随发布配置走。证据：`docs/knowledge-base-publications.md`。

### F6 演示网页
- FR-22 侧边栏显示发布身份、Reviewer 提示词版本、凭据存在性（永不显示值）。证据：`app.py`。
- FR-23 dry-run 默认（零调用）；真实运行需显式取消勾选。证据：AppTest。
- FR-24 失败只显示安全类别，不输出细节。证据：`app.py` 异常分支。

### F7 交付与交接
- FR-25 ECS 直接部署配方（不用 Docker，2026-08-10 与老师确认）：venv 依赖 + systemd + nginx + 安全组；Dockerfile 保留为备选交付物。证据：handoff 文档部署清单。
- FR-26 交接文档：架构不变式、部署清单、R5 转正重发布流程。证据：`docs/handoff-ontology-team-2026-08-06.md`。
- FR-27 全部提交推送 GitHub；tracked 树干净（验收/进度类未入库工件另见 §9 O-6/O-7）。证据：`2724f02` 及后续。

### F8 本地优先编排线（并行交付）
- FR-28 `LocalLLMOrchestrator` + `SessionStore`：服务端 chat 门禁、审计元数据、租户/用户级会话隔离；demo 主路径用三角色 runner，此线为并行交付的 local-first 编排层。证据：`llm/orchestrator.py`、`session_store.py`、`test_local_llm_orchestrator.py`、`docs/architecture/llm-integration-local-first.md`。

## 6. 非功能需求（NFR）

- NFR-1 安全 fail-closed：所有门禁"宁可断电，不可起火"；拒绝时不回显敏感值。
- NFR-2 成本纪律：真实调用逐轮批准、预算封顶；全周期约 45 次调用/约 15 万 tokens（自报口径，无逐次台账）。
- NFR-3 不可变性：提示词/ schema / 考卷 / 导出均冻结或哈希钉死；变更走升版+复验。
- NFR-4 确定性：导出脚本字节级确定（双跑一致测试）。
- NFR-5 隐私：输出与台账不含密钥、客户数据； denied-markers 校验入验证器。
- NFR-6 可交接性：任何交付物须"他人可接手"（交接文档 + 部署清单）。

## 7. 验收标准与证据映射

| 验收门 | 结果 | 证据 |
|---|---|---|
| 契约/收敛门禁 | 14 项 + 契约测试全绿 | `38fcc6d` |
| Reviewer 考卷 | 第一轮 8/8；确认轮 7/7 有效 + 1 NETWORK；补证 1/1；零判断错误 | 入库运行记录（重构版）`tests/fixtures/llm_eval/runs/run-records-2026-08-06.md` |
| 检索 5 硬门 | 全过 @0.60 | 百炼 eeirxr7djz；召回百分比入台账（截图未入库） |
| 路由三层 | 全 fail-closed | 入库运行记录（重构版）；NOT_ALLOWED 兼有真实记录与离线参数化覆盖 |
| 离线回归 | 588 收集：587 passed / 1 skipped | pytest |
| E2E | initial_render OK；chat 安全回退正确 | 入库运行记录（重构版） |

## 8. 里程碑回溯

L1 契约 → 连通烟雾 → 三角色管道 → 收敛对齐（canonical R5）→ Reviewer 四轮校准（v6→v9）→ 路由安全真实验证 → 知识库 S2/S3 → UI/交接 → ECS 直接部署（不用 Docker，上云移交本体团队）。

## 9. 开放项与已知取舍

- O-1 R5 待本体团队人工审核转正；转正走重发布流程（FR-21/FR-26）。
- O-2 Triage flash 误路由：保留并兜底；升级模型或提示词为独立评估项。
- O-3 上云由本体团队执行（实例/公网 IP 等基础设施标识符存内部台账，不入库）；机器租期至 2026-09-12，需续费决策。
- O-4 canonical/Hannah 集成分支未并入 main（团队排序决策，LLM 侧已先行）。
- O-5 Executor 首轮偶发 `limitations_included` 类型错：内置修复循环消化，设计内。
- O-6 main 工作区对 `publication_manifest.json`（626cfbde…）的根级校验当前抛 PackageDriftError（合并顺序漂移）；运行时经 bundle 级校验安全。待 canonical/Hannah 合并 + 文件对齐后重跑至 MANIFEST_OK。
- O-7 ECS 直接部署待首跑验证（2026-08-10 与老师确认不用 Docker：uv sync --frozen + systemd + nginx；Dockerfile 保留为备选交付物，未经验证）；首跑风险见交接文档部署清单。

## 10. 术语表

本体/规则卡/verdict、release identity、fail-closed、冻结考卷、混合 RAG、dry-run、预算账本、安全诊断码。
