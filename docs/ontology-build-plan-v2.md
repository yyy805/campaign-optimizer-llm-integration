# 本体搭建执行计划 v2（Review-only）

**状态：当前权威执行计划**

**更新日期：2026-08-04**

架构依据：[`architecture/ontology-review-only-v2.md`](architecture/ontology-review-only-v2.md)。

## 1. 已完成基线

- 24张概念卡和资产manifest；
- 7张规则卡，R1–R6 ACTIVE、R7 RETIRED；
- 概念/规则/护栏Schema；
- 真实trigger求值、baseline、实体和时间粒度校验；
- Review、反馈、方案决策和LLM交换契约；
- GOOD/FINE/BAD运行时置信度状态机；
- 8份MTA材料清单检查；
- GitHub私有仓库、PR模板和离线CI；
- G1/G2标记为 `PENDING_INPUT_CONTRACT`：保留设计意图，但在最终方案契约补齐出价、每日预算等字段前不参与运行时评价。

## 2. 当前关键缺口

系统已具备 R5-only Review Engine v1：可自动读取最终方案和评价事实、确定性匹配 R5，并生成和复核 `ontology_review`。其余规则接入、反馈持久化与真实最终方案联调仍待完成。

MTA 组提供的 `initial_budget_recommendation.json` 是 `INITIAL_SEED`、`is_optimized=false` 的中间产物，只作为上游模型产物归档，不直接触发本体 Review。当前不建设 MTA→本体 Evidence Adapter；只有完整小模型链路的最终方案不符合 `final_plan` 契约时，才建设不增加业务计算的薄映射层。

## 3. 工作包与顺序

### WP1：Review Engine（R5 v1已完成）

**负责人：本体团队**

新增建议路径：

```text
campaign_optimizer/ontology/review_engine.py
```

实现：

- 加载ACTIVE规则和当前confidence_state；
- 按plan_item筛选实体/时间粒度适用规则；
- 复用权威条件求值器；
- 自动生成五类verdict；
- 自动生成matched facts、缺失证据、限制和置信度快照；
- 汇总overall_verdict；
- 输出前调用Contract Gate。

验收：相同输入重复运行结果完全一致；不调用Qwen或任何外部API。

### WP2：上游产物归档与最终方案契约（替代旧 MTA Evidence Adapter）

**负责人：小模型链路团队提供契约，本体团队维护消费边界**

实现边界：

- `INITIAL_SEED` 等中间结果原样保存为 `model_artifacts`，保留版本、哈希、周期、来源和上下游关联；
- 只有通过 `final_plan` 契约的完整小模型链路最终方案才能触发 Review Engine；
- Review 所需公开事实由最终链路契约提供，不从中间 MTA 文件推断或补算；
- 如最终输出仅存在字段命名/封装差异，可以建设薄 Adapter；Adapter 不得计算 contribution_share、spend_share、attribution_divergence 或其他业务指标。

验收：中间产物不能误触发 Review；最终方案及公开证据能被契约 Gate 验证并追溯到上游 artifact。

### WP3：R5端到端纵向切片

**负责人：本体团队；后端协助触发**

输入：一份通过契约验证的最终预算方案和最终链路公开评价事实。

输出：Review Engine自动生成并经Gate验证的 `ontology_review.json`。

必须测试六种路径：CONFLICT、SUPPORT、NOT_APPLICABLE、INSUFFICIENT_EVIDENCE、规则不命中、UNVERIFIED。

验收命令：

```powershell
uv run pytest tests/test_review_engine.py -q
uv run pytest -q
```

Demo CLI：

```powershell
uv run python scripts/generate_ontology_review.py `
  tests/fixtures/plan_a/final_plan.demo.json `
  ontology_review.generated.json `
  --confidence-state tests/fixtures/plan_a/confidence_state.r5.demo.json
```

未提供可验证的运行时 `confidence_state` 时，引擎只生成 `INSUFFICIENT_EVIDENCE`，不得用规则卡基础置信度冒充当前运行状态。

### WP4：运行时数据库与反馈持久化

**负责人：本体团队/后端团队**

本地 Demo 使用 SQLite，部署目标为 PolarDB for PostgreSQL。Git 中的本体发布包仍是概念卡、规则卡和反馈策略的权威来源；数据库保存运行快照、反馈状态和审计记录。

首批保存：

- model artifacts；
- final plan及条目；
- ontology review及条目；
- feedback events与confidence state；
- plan ACCEPT/REJECT事件。

验收：重启程序后置信度和已处理反馈仍存在；重复事件不重复计分，篡改事件被拒绝。

### WP5：本体维护指南

**负责人：本体团队**

新增：

```text
docs/ontology-maintenance-guide.md
```

覆盖新增/修改概念和规则、版本历史、manifest、检查脚本、PR、状态迁移、Demo阈值和禁止自动造规则。

验收：未参与搭建的成员可按指南新增一张测试卡并让CI通过。

### WP6：扩展其他规则

R5跑通后，再按真实上游字段逐条接入R1–R6。没有对应公开字段的规则保持不可求值，不通过造假Fixture宣称已完成。

R7必须等待真实预测模块和字段合同；不能直接恢复旧卡。

## 4. 与大模型团队的交接点

本体团队交付：

```text
final_plan.json
ontology_review.json
llm_context.json
相关Schema和Golden Fixture
```

大模型团队负责：

- Qwen API客户端与配置；
- 翻译/解释Agent；
- 评审Agent和有界回炉；
- 问题范围分诊；
- 引用忠实性、拒答和Fallback测试。

大模型团队不得负责：

- trigger_condition计算；
- baseline数值比较；
- SUPPORT/CONFLICT裁决；
- 修改ontology_review；
- 自动创建规则卡。

## 5. 优先级

```text
P0  Review Engine
P0  上游产物归档边界与最终方案契约确认
P0  R5端到端测试
P0  SQLite运行时数据库与feedback持久化
P1  PolarDB迁移和真实环境烟测
P1  维护指南
P2  R1–R6其他适配
P2  护栏重新激活评估
P2  R7真实预测替换
```

在WP1–WP4完成前，不继续扩充概念卡和规则卡数量。

## 6. 当前完成定义

本体Demo只有同时满足以下条件才算“跑通”：

- 输入最终方案和公开事实；
- 自动生成ontology_review，不使用手写评价Fixture；
- 规则真实命中和动作映射均可复现；
- 所有输出通过Contract Gate；
- 页面和Qwen只能消费验证后的快照；
- 用户反馈可以安全持久化；
- CI在新环境可重复通过。
