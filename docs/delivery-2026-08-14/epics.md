---
stepsCompleted: [step-01-validate-prerequisites, step-02-design-epics, step-03-create-stories]
inputDocuments:
  - _bmad-output/planning-artifacts/prds/prd-ai-workflow-lab-2026-08-06/prd.md
  - _bmad-output/planning-artifacts/prds/prd-ai-workflow-lab-2026-08-06/addendum.md
---

# AI Workflow Lab（大模型接入） - Epic Breakdown

## Overview

This document provides the complete epic and story breakdown for AI Workflow Lab（大模型接入 / Campaign Optimizer 解释员）, decomposed from the as-built PRD（final, 2026-08-06）与 addendum（架构决策替代输入；正式架构文档为阶段 3 产出，届时对账）。回溯式：所有 story 均映射已交付代码与证据。

## Requirements Inventory

### Functional Requirements

FR1: 五份输出契约模板（请求/上下文/工作流输出/本体审核/最终方案），缺字段或错类型即拒收。
FR2: 发布身份六字段任一篡改，在构造 provider 前失败且不回显伪造值。
FR3: 发布包校验和验证，资产漂移即 PackageDriftError。
FR4: 待审核规则（status≠ACTIVE）不得作为决定性证据进入上下文（INACTIVE_RULE）。
FR5: 候选与已提交审核 verdict 矛盾时 fail-closed。
FR6: Executor 起草解释，输出过 OutputGuard 与交换契约（限制完整披露、数值接地）。
FR7: Reviewer 以 Function Calling 唯一通道提交决策（PASS/REVISE/REJECT），content 为空、工具调用恰一次、参数过本地权威 schema。
FR8: Triage 仅在 chat 模式且硬路由无锚定时介入；弃权即安全回退。
FR9: 预算账本：每候选每角色 ≤2 次、triage ≤1、总量封顶；超限 BUDGET_DENIED。
FR10: 角色提示词与工具 schema 哈希钉死；改一字必须升版复验。
FR11: 冻结考卷 8 案例、三类标签 + 安全类，decision match 为硬门。
FR12: 考卷钉住 canonical R5@2.0-campaign-pending 发布身份；manifest 漂移即失败。
FR13: 行级安全诊断：记录模型引用的违规码与修订动作形状，不含 answer 原文。
FR14: 验收门：每题至少两次正确判断；单轮不足以为证。
FR15: 硬路由：复合/超模板解释问句 fail-closed 拒答（零调用）。
FR16: 意图白名单：triage 路由意图越界即 SYSTEM_FALLBACK。
FR17: 50 题路由安全数据集离线守护。
FR18: 混合架构不变式：权威上下文走 release-pin 确定性投影；检索结果一律不可信数据。
FR19: 知识库导出为忠实投影（元数据头 + 规则卡原文），状态显式（ACTIVE/PENDING/RETIRED）。
FR20: 检索硬门：查 R5 只返回 R5；pending/retired 标记必须 surfaced；越界提问无结果。
FR21: 发布台账：每次发布记录 KB ID ↔ package_checksum；阈值随发布配置走。
FR22: 演示网页侧边栏显示发布身份、Reviewer 提示词版本、凭据存在性（永不显示值）。
FR23: dry-run 默认（零调用）；真实运行需显式取消勾选。
FR24: 失败只显示安全类别，不输出细节。
FR25: ECS 直接部署配方（不用 Docker）：venv 依赖 + systemd + nginx + 安全组；Dockerfile 保留为备选交付物。
FR26: 交接文档：架构不变式、部署清单、R5 转正重发布流程。
FR27: 全部提交推送 GitHub；tracked 树干净。
FR28: 本地优先编排线（LocalLLMOrchestrator + SessionStore）：服务端 chat 门禁、审计元数据、租户/用户级会话隔离；demo 主路径用三角色 runner。

### NonFunctional Requirements

NFR1: 安全 fail-closed：所有门禁"宁可断电，不可起火"；拒绝时不回显敏感值。
NFR2: 成本纪律：真实调用逐轮批准、预算封顶（自报口径，无逐次台账）。
NFR3: 不可变性：提示词/schema/考卷/导出均冻结或哈希钉死；变更走升版+复验。
NFR4: 确定性：导出脚本字节级确定（双跑一致测试）。
NFR5: 隐私：输出与台账不含密钥、客户数据；denied-markers 校验入验证器。
NFR6: 可交接性：任何交付物须"他人可接手"（交接文档 + 部署清单）。

### Additional Requirements

- 棕地项目（brownfield）：现有仓库即起点（uv + pytest + Streamlit），无 starter template；Epic 1 不从零脚手架。
- 部署：**不用 Docker**（2026-08-10 与老师确认）：ECS 直跑——git clone → uv sync --frozen（阿里云 PyPI 索引）→ systemd 跑 Streamlit headless 保活 → nginx 反代 → 安全组开 8501；ECS 上海区域跨区调北京百炼；环境变量经 systemd EnvironmentFile 外置，不入库。Dockerfile 保留为备选交付物。
- 中国网络：uv/pip 走阿里云镜像（UV_INDEX_URL / pip -i mirrors.aliyun）。
- 版本对应不变式：知识库每次发布须在台账记录 KB ID ↔ package_checksum；相似度阈值 0.60 是发布配置的一部分。
- 提示词谱系不可原地编辑：v6→v9 逐版继承，配置 agent_roles.vN.json 哈希钉死。
- 预算公式：max_provider_calls_v12(rounds, triage) = 4*(rounds+1)+triage；评测 ledger = 2×案例数。
- 证据入库约定：真实运行记录以脱敏重构版入库（tests/fixtures/llm_eval/runs/），标注来源。
- 开放项约束：O-6 工作区漂移待合并复验；O-7 ECS 直接部署待首跑验证——相关 story 须标注"待外部条件"。

### UX Design Requirements

无。UX 设计阶段有意跳过：UI 为功能性 demo（Streamlit 单页），理由见 PRD §4。

### FR Coverage Map

FR1: Epic 1 - 五份输出契约模板，越框即拒
FR2: Epic 1 - 发布身份六字段防篡改，调模型前失败且不回显
FR3: Epic 1 - 发布包校验和验证，漂移即 PackageDriftError
FR4: Epic 1 - 待审核规则不得作为决定性证据（INACTIVE_RULE）
FR5: Epic 1 - 候选与已提交 verdict 矛盾即 fail-closed
FR6: Epic 2 - Executor 起草过 OutputGuard 与交换契约
FR7: Epic 2 - Reviewer Function Calling 唯一通道 + 本地权威 schema
FR8: Epic 2 - Triage 介入条件与弃权安全回退
FR9: Epic 2 - 预算账本封顶与 BUDGET_DENIED
FR10: Epic 2 - 提示词/ schema 哈希钉死，改一字升版复验
FR11: Epic 3 - 冻结考卷 8 案例三类标签 + 安全类，decision match 硬门
FR12: Epic 3 - 考卷钉住 canonical 发布身份，漂移即失败
FR13: Epic 3 - 行级安全诊断（违规码 + 动作形状，不含原文）
FR14: Epic 3 - 验收门：每题至少两次正确判断
FR15: Epic 1 - 硬路由复合问句零调用拒答
FR16: Epic 1 - 意图白名单越界即 SYSTEM_FALLBACK
FR17: Epic 1 - 50 题路由安全数据集离线守护
FR18: Epic 4 - 混合 RAG 不变式：权威走确定性投影
FR19: Epic 4 - 导出忠实投影，状态显式
FR20: Epic 4 - 检索硬门（R5 只返回 R5 等五门）
FR21: Epic 4 - 发布台账 KB ID ↔ package_checksum，阈值随发布走
FR22: Epic 5 - 侧边栏显示身份/提示词版本/凭据存在性
FR23: Epic 5 - dry-run 默认，真实运行显式开启
FR24: Epic 5 - 失败只显示安全类别
FR25: Epic 5 - Dockerfile 含 bundles，headless Streamlit
FR26: Epic 5 - 交接文档三要素
FR27: Epic 5 - 全部推送，tracked 树干净
FR28: Epic 2 - 本地优先编排线（orchestrator + session store）

## Epic List

### Epic 1: 可信解释底座
用户得到的要么是合规解释、要么是明确的"无法回答"，永远不是编造内容。
**FRs covered:** FR1–FR5, FR15–FR17
**证据锚点:** 38fcc6d；run-records R4

### Epic 2: 三角色协作解释生成
系统能起草、自审、且不超预算；含本地优先编排线。
**FRs covered:** FR6–FR10, FR28
**证据锚点:** runner 谱系（基座 + v6–v13）；agent_roles.v15

### Epic 3: 可测量的 Reviewer 判断
团队能像考新员工一样"考"质检员：冻结考卷 + 安全诊断 + 验收门。
**FRs covered:** FR11–FR14
**证据锚点:** c971b0b；54c2e4e；run-records R1–R3

### Epic 4: 权威知识与补充阅览室
规则查询准确、不污染、版本可追溯。注：继承 O-6（工作区漂移待合并复验）。
**FRs covered:** FR18–FR21
**证据锚点:** 40002ba；docs/knowledge-base-publications.md

### Epic 5: 演示与交接
利益相关者看得到、本体团队接得走。故事组 A 演示线（FR22–24，受众：老师/观看者）；故事组 B 交接线（FR25–27，受众：本体团队/运维，继承 O-7 ECS 直接部署待首跑验证）。
**FRs covered:** FR22–FR27
**证据锚点:** 19d30f0；5c4e6d5；2724f02

## Epic 1: 可信解释底座

用户得到的要么是合规解释、要么是明确的"无法回答"，永远不是编造内容。

### Story 1.1: 契约模板与校验基线

As a 安全负责人,
I want 所有模型输出先过模板校验,
So that 越框内容被立即拒收.

**Acceptance Criteria:**

**Given** 五份契约（请求/上下文/工作流输出/本体审核/最终方案）
**When** 运行契约测试组
**Then** 有效样本通过、缺字段/错类型样本被拒
**And** 证据锚点：campaign_optimizer/schemas/、contracts/、契约测试（FR1）

### Story 1.2: 发布身份防篡改

As a 安全负责人,
I want 六个身份字段任一被篡改都在调模型前失败,
So that 伪造版本进不了管道.

**Acceptance Criteria:**

**Given** 六字段参数化篡改（ontology_version/rule_version/engine_version/schema_version/source_commit/package_checksum）
**When** 构建请求
**Then** 抛 ContractValidationError 且不回显伪造值
**And** 资产漂移即 PackageDriftError；证据锚点：release_pin.py、test_llm_convergence_integration.py（FR2, FR3）

### Story 1.3: 待审核规则与 verdict 矛盾 fail-closed

As a 产品负责人,
I want 未批准的规则不当证据、矛盾的候选被拒,
So that 系统永不"替本体说话".

**Acceptance Criteria:**

**Given** R5@2.0（PENDING_HUMAN_REVIEW）
**When** 作为决定性证据检索
**Then** INACTIVE_RULE 拒绝
**And** 候选篡改已提交 verdict 时 packet 构造 fail-closed 且不回显；证据锚点：retriever.py、收敛门禁测试（FR4, FR5）

### Story 1.4: 路由硬门禁与意图白名单

As a 用户,
I want 复合或恶意提问被零消耗礼貌拒绝,
So that 门永远锁着.

**Acceptance Criteria:**

**Given** 复合解释问句
**When** chat 模式发送
**Then** REFUSED 且 0 调用
**And** 意图越界即 SYSTEM_FALLBACK；50 题路由集离线绿；证据锚点：intent_policy.py、routing-safety.json、run-records R4（FR15–17）

## Epic 2: 三角色协作解释生成

系统能起草、自审、且不超预算；含本地优先编排线。

### Story 2.1: Executor 起草与 OutputGuard

As a 质量负责人,
I want 草稿过交换契约（限制完整披露、数值接地）才到质检,
So that 低质草稿早过滤.

**Acceptance Criteria:**

**Given** pending 上下文
**When** Executor 输出
**Then** 限制完整披露、高危数值在 answer 接地
**And** 首轮类型错被内置修复循环消化（设计内）；证据锚点：output_guard.py、exchange.py、run-records R5（FR6）

### Story 2.2: Reviewer Function Calling 通道

As a 安全负责人,
I want 质检员经唯一函数通道提交决策、参数过本地权威 schema,
So that 模型输出永远受结构约束.

**Acceptance Criteria:**

**Given** v13 调用
**When** 模型返回
**Then** content 为空、工具调用恰一次、arguments ≤ 上限、schema/binding（digest/allowlist/动作语义）校验通过
**And** 证据锚点：three_role_runner_v13.py、reviewer_binding_v13.py（FR7）

### Story 2.3: Triage 介入与预算账本

As a 成本负责人,
I want 调用有上限、路由有兜底,
So that 不烧穿也不放行.

**Acceptance Criteria:**

**Given** chat 模式且硬路由无锚定
**When** triage 介入
**Then** 弃权或越界均安全 FALLBACK
**And** 每候选每角色 ≤2、triage ≤1、总量封顶、超限 BUDGET_DENIED；证据锚点：BudgetLedgerV12、run-records R4（FR8, FR9）

### Story 2.4: 提示词版本盖章

As a 质量负责人,
I want 提示词/schema 改动等于升版+复验,
So that 没人能偷偷改质检标准.

**Acceptance Criteria:**

**Given** 任意一字改动提示词
**When** 加载配置
**Then** 哈希 mismatch 即 ValueError
**And** v6→v9 谱系逐版可复；证据锚点：agent_roles.v15.json、load_role_configuration（FR10）

### Story 2.5: 本地优先编排线

As a 平台负责人,
I want 已交付的 local-first 编排层带会话隔离与审计元数据,
So that 主路径与编排线各有定位.

**Acceptance Criteria:**

**Given** orchestrator chat 请求
**When** 执行
**Then** 服务端门禁 + OrchestrationResult/AttemptMetadata 审计、租户/用户级会话隔离
**And** demo 主路径用三角色 runner；证据锚点：orchestrator.py、session_store.py、test_local_llm_orchestrator.py（FR28）

## Epic 3: 可测量的 Reviewer 判断

团队能像考新员工一样"考"质检员：冻结考卷 + 安全诊断 + 验收门。

### Story 3.1: 冻结考卷与守护测试

As a 质量负责人,
I want 考卷预先冻结、监考被证明有效,
So that 考试能测出真能力而非运气.

**Acceptance Criteria:**

**Given** 8 案例/三类标签 + 安全类
**When** 运行验证器与守护测试
**Then** 冻结计数成立、六种变异（计数/重复ID/破引用/标签错配/RULE_FIELD/verdict 篡改）全拒
**And** 每案例期望 decision 合约可达；证据锚点：reviewer_judgment_v1（FR11, FR12）

### Story 3.2: 安全诊断与评测脚本

As a 质量负责人,
I want 评测脚本默认彩排、真实跑门禁化、行级诊断,
So that 失败能归因且不泄原文.

**Acceptance Criteria:**

**Given** 默认运行
**When** 启动脚本
**Then** 零调用只报计划
**And** --real 显式开启；诊断字段仅含违规码与动作形状（operation/source/target）；证据锚点：run_reviewer_judgment_eval_v14.py + tests（FR13）

### Story 3.3: 验收运行与关闭

As a 利益相关者,
I want 验收门为"每题至少两次正确判断",
So that 信任能当场建立.

**Acceptance Criteria:**

**Given** v9 配置
**When** 真实评测
**Then** 第一轮 8/8；确认轮 7/7 有效 + 1 NETWORK（非判断）；补证 1/1；累计零判断错误
**And** 证据锚点：run-records R1–R3（FR14）

## Epic 4: 权威知识与补充阅览室

规则查询准确、不污染、版本可追溯。注：继承 O-6（工作区漂移待合并复验）。

### Story 4.1: 确定性导出与忠实投影

As a 内容负责人,
I want 导出为规则卡原文投影、状态显式,
So that 阅览室永不改写档案.

**Acceptance Criteria:**

**Given** 7 张规则卡
**When** 双跑导出脚本
**Then** 字节级一致（确定性测试）
**And** 元数据头含 rule_id/version/status/source_release；ACTIVE/PENDING/RETIRED 显式；证据锚点：export_knowledge_base_v1.py、kb_export/v1、test_kb_export_v1.py（FR18, FR19）

### Story 4.2: 知识库发布与检索验收

As a 用户,
I want 规则检索准确、不污染,
So that 查什么得什么.

**Acceptance Criteria:**

**Given** 百炼库 @ 阈值 0.60
**When** 跑 5 硬门
**Then** 查 R5 只返回 R5；R5 带 PENDING_HUMAN_REVIEW；R7 带 RETIRED；限制命中；越界提问无结果
**And** 阈值随发布配置走；证据锚点：knowledge-base-publications.md、run-records（FR20）

### Story 4.3: 发布台账与重发布流程

As a 本体团队,
I want 每次发布进台账、R5 转正走重发布流程,
So that 版本对应永不失.

**Acceptance Criteria:**

**Given** 任一知识库发布
**When** 记录
**Then** KB ID ↔ package_checksum 成行入台账
**And** R5 转正重发布流程（导出→上传→0.60→重验收→记账）入交接文档；证据锚点：knowledge-base-publications.md、handoff 文档（FR21）

## Epic 5: 演示与交接

利益相关者看得到、本体团队接得走。故事组 A 演示线（受众：老师/观看者）；故事组 B 交接线（受众：本体团队/运维，继承 O-7）。

### Story 5.1: 演示网页（组 A 演示线）

As a 老师/观看者,
I want 网页上看到系统状态与安全行为,
So that 演示不失控.

**Acceptance Criteria:**

**Given** 打开网页
**When** 查看侧边栏
**Then** 发布身份/Reviewer 提示词版本/凭据存在性三项（永不显示值）
**And** dry-run 默认零调用；失败只显示安全类别；证据锚点：app.py + AppTest（FR22–24）

### Story 5.2: ECS 直接部署配方（组 B 交接线，不用 Docker）

As a 本体团队,
I want 按配方在 ECS 上直接部署,
So that 部署不依赖作者.

**Acceptance Criteria:**

**Given** 交接文档部署清单
**When** 在 ECS 执行（git clone → uv sync --frozen 阿里云索引 → systemd 跑 Streamlit headless 保活 → nginx 反代 → 安全组 8501）
**Then** 服务可访问且 release-pin 自校验通过（仓库自带 .ontology_bundles 与 tests/fixtures）
**And** 继承 O-7：首跑待验证（首跑即首验）；Dockerfile 保留为备选交付物；证据锚点：handoff 文档（FR25）

### Story 5.3: 交接文档与全量推送（组 B 交接线）

As a 本体团队,
I want 读交接文档即可接手,
So that 零沟通成本上云.

**Acceptance Criteria:**

**Given** 交接文档
**When** 阅读
**Then** 架构不变式/部署清单/R5 转正重发布流程三要素齐
**And** 全部提交推送、tracked 树干净；证据锚点：2724f02、459c97f（FR26, FR27）
