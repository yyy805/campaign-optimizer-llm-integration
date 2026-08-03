# 本体搭建执行计划 v2（Review-only）

**状态：当前权威执行计划**

**日期：2026-08-03**

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
- G1/G2安全退役，避免使用不存在或量纲不一致的字段。

## 2. 当前关键缺口

目前系统能验证一份 `ontology_review` 是否真实、合法，但该文件仍由Fixture预先写好。缺少自动读取最终方案和事实、遍历规则并生成评价的Review Engine。

这意味着本体“定义和验真”已经完成，尚未完成“自动出评价”。

## 3. 工作包与顺序

### WP1：Review Engine

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

### WP2：MTA Evidence Adapter

**负责人：本体团队，MTA团队提供字段解释**

新增建议路径：

```text
campaign_optimizer/adapters/mta_review_evidence.py
scripts/build_mta_review_evidence.py
```

实现：

- 读取manifest声明的MTA结果文件；
- 将MTA公开字段映射为concept_id；
- 输出标准review_evidence；
- 保留source、period、entity、unit和模型口径；
- 不复刻Markov/Shapley内部计算。

首批只满足R5所需的：

- contribution_share；
- spend_share；
- attribution_divergence。

验收：同一报告窗口和同一channel的三个事实可以被R5共同消费。

### WP3：R5端到端纵向切片

**负责人：本体团队；后端协助触发**

输入：一份最终预算方案和MTA公开结果。

输出：Review Engine自动生成并经Gate验证的 `ontology_review.json`。

必须测试六种路径：CONFLICT、SUPPORT、NOT_APPLICABLE、INSUFFICIENT_EVIDENCE、规则不命中、UNVERIFIED。

验收命令：

```powershell
uv run pytest tests/test_r5_review_engine_e2e.py -q
uv run pytest -q
```

### WP4：反馈持久化

**负责人：本体团队/后端团队**

Demo使用SQLite保存：

- confidence_state；
- processed feedback digest；
- 状态变更时间；
- PENDING_HUMAN_REVIEW原因。

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
P0  MTA Evidence Adapter
P0  R5端到端测试
P1  feedback SQLite持久化
P1  维护指南
P2  R1–R6其他适配
P2  护栏重新激活评估
P2  R7真实预测替换
```

在WP1–WP3完成前，不继续扩充概念卡和规则卡数量。

## 6. 当前完成定义

本体Demo只有同时满足以下条件才算“跑通”：

- 输入最终方案和公开事实；
- 自动生成ontology_review，不使用手写评价Fixture；
- 规则真实命中和动作映射均可复现；
- 所有输出通过Contract Gate；
- 页面和Qwen只能消费验证后的快照；
- 用户反馈可以安全持久化；
- CI在新环境可重复通过。
