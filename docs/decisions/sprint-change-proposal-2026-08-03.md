# Sprint Change Proposal：本体改为Review-only

**状态：已批准并实施文档纠偏**

**日期：2026-08-03**

## 1. Issue Summary

老师进一步明确：本体当前只审核小模型链路的最终方案，不生成第二套方案，不审核每个中间模型结果，不自动执行方案。用户只能接受/拒绝方案，并对本体评价选择GOOD/FINE/BAD；反馈不自动产生新规则。

旧PRD、Epic和架构仍包含本体生成建议、自动执行、LLM候选规则和中间诊断等当前Demo设计，容易导致团队继续实现错误范围。

## 2. Impact Analysis

### PRD/Epic

- FR1“生成结构化诊断”调整为“对最终方案生成结构化Review”；
- FR5–FR7自动执行、审批和回滚移出当前Demo；
- 旧R-042自动执行切片保留为历史技术实验，不代表当前产品链路；
- 反馈只更新运行时置信度，不产生或直接修改规则。

### Architecture

- 推理对象由原始数据/中间结果改为最终方案和公开review evidence；
- 本体输出由建议改为ontology_review；
- LLM从诊断/裁决者调整为翻译、解释、受限问答和忠实性评审者；
- 确定性Review Engine由本体团队负责；
- 自动执行层不进入当前Demo。

### UX

- 页面显示最终方案、本体评价、接受/拒绝和评价反馈；
- 不提供预算修改控件或重新优化入口；
- 问答只解释当前方案、评价、规则、事实、置信度和限制。

### Technical

- 已完成卡片、Schema和Gate无需回滚；
- 需要新增Review Engine和MTA Evidence Adapter；
- 需要将手写review Fixture升级为自动生成的端到端测试；
- G1/G2状态为 `PENDING_INPUT_CONTRACT`，待最终方案契约补齐出价、每日预算等字段后再评估激活。

## 3. Recommended Approach

选择：**Direct Adjustment**。

不回滚现有契约和测试。保留可复用的规则求值、反馈状态机、版本控制和安全Gate；停止扩卡，优先补齐自动Review执行链。

风险等级：中。主要风险是旧文档继续被误用，以及本体团队和大模型团队对Review Engine归属理解不一致。

## 4. Detailed Changes

- 新增 `docs/architecture/ontology-review-only-v2.md` 作为权威架构；
- 新增 `docs/ontology-build-plan-v2.md` 作为权威执行计划；
- 旧架构、旧搭建计划和机制表标记为历史文档；
- Review Engine归本体团队；
- Qwen Agent归大模型团队，禁止产生或修改结构化verdict；
- 首个纵向切片只跑R5。

## 5. Implementation Handoff

### 本体团队

Review Engine、MTA事实适配、R5端到端、反馈持久化、维护指南。

### 大模型团队

Qwen API、翻译/解释Agent、评审Agent、有界回炉、分诊、拒答和Fallback测试。

### 后端/UI团队

上传触发、组件编排、页面展示和事件存储。

## 6. Success Criteria

- 不再由Fixture手写本体评价；
- R5可以从方案与MTA事实自动产生Review；
- Qwen不能改变结构化方案或verdict；
- 所有输出通过离线Gate和CI；
- 团队只以v2文档作为当前实现依据。

## 7. Checklist Result

- [x] 触发原因与老师决定明确
- [x] PRD/Epic/架构/UX影响已分析
- [x] Direct Adjustment可行，无需回滚
- [x] 团队职责边界明确
- [x] 当前MVP范围重新冻结
- [!] 尚未建立统一sprint-status.yaml
- [x] Review Engine与R5纵向切片已实施（确定性生成、权威复核与离线测试）
