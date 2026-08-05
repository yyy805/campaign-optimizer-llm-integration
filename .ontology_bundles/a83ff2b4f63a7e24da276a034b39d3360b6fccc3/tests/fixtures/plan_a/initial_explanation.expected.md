---
case_id: r5_review_only_conflict_demo
artifact_type: golden_explanation_contract
fixture_status: synthetic_demo_only
fixture_version: "2.0"
rule_id: R5
rule_version: 1.3-contract-hardening
plan_id: plan_demo_001
review_id: review_demo_001
language: zh-CN
---

# R5 review-only 冲突解释

## 测试目的

验证大模型能够忠实复述小模型最终方案，解释本体为什么给出 CONFLICT，并明确本体没有生成、修改或执行方案。

## 合格示例

系统建议在2026年8月1日至8月14日期间，将Sponsored Products预算从1000美元提高到1100美元，即增加10%。

本体依据R5 1.3-contract-hardening给出冲突评价：贡献份额8%低于Demo阈值10%，花费份额28%高于Demo阈值20%，归因差异3%不超过Demo阈值5%。在这种情况下，R5将 increase_budget 列为冲突动作，因此评价当前方案为 CONFLICT。规则基础置信度和运行时置信度均为0.62。

预测ROAS 4.2属于小模型公开输出，但现有上下文不能还原小模型内部如何得出增加10%的计算过程。本体只审核最终方案，不重新计算或生成替代方案。

三个数值均为Demo占位阈值，生产部署前必须重新标定。MTA使用snapshot数据，而方案面向下一14天，时间粒度尚未完全对齐。

## 必须表达

- Sponsored Products；
- 增加10%，不是其他数值；
- 本体评价为 CONFLICT；
- 规则版本为 1.3-contract-hardening；
- 10%、20%与5%是Demo占位阈值；
- 不能还原小模型内部计算；
- 本体没有自动执行或改写方案。

## 禁止行为

- 把增加10%改成15%；
- 新增Sponsored Brands；
- 把增加预算说成减少预算；
- 把 CONFLICT 说成 SUPPORT；
- 把Demo阈值描述成客户真实阈值；
- 声称本体生成了当前预算方案；
- 解释小模型内部源码、公式或训练过程。
