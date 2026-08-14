---
title: Sprint 状态表 — LLM Integration（as-built）
author: John（产品经理）
status: final
created: 2026-08-14
sources: [epics.md, IMPLEMENTATION-RECORDS(final), QA-E2E-REPORT(final)]
---

# Sprint 状态表（as-built，2026-08-14）

## 总览

- 故事总数 18；**done 18**；其中 2 条含待外部条件子项（1.2 注记 O-6、5.2 子项 O-7）。
- 进行中 0；受阻 0（全部缺口 fail-closed 兜底并挂触发器，见 QA §5）。
- 验收：考卷零判断错误；路由三层活验证；检索五门 @0.60；离线 571 通过/1 跳过。

## 明细

| 故事 | 状态 | 证据 | 注记 |
|---|---|---|---|
| 1.1 契约模板与校验基线 | done | 契约测试四件套 | — |
| 1.2 发布身份防篡改 | done | 38fcc6d | 含 O-6 注记（工作区根校验待合并复验） |
| 1.3 待审核/矛盾 fail-closed | done | 38fcc6d | — |
| 1.4 路由硬门禁与白名单 | done | routing 50 题 + 活探针 R4 | — |
| 2.1 写手与输出守卫 | done | de07eb4 + R5 | 修复循环为设计内 |
| 2.2 质检员函数通道 | done | v13 测试 + 伪造桩 | — |
| 2.3 分诊与预算账本 | done | v12 预算测试 + R4 | — |
| 2.4 提示词盖章 | done | agent_roles.v15 | — |
| 2.5 本地优先编排线 | done | 80fe8f6/eeca02a | AD-11 显式豁免 |
| 3.1 冻结考卷与守护 | done | c971b0b | — |
| 3.2 安全诊断与脚本 | done | 4fee4e3 | — |
| 3.3 验收运行与关闭 | done | 54c2e4e + R1–R3 | NETWORK 非判断错误 |
| 4.1 确定性导出 | done | 40002ba | — |
| 4.2 检索验收 | done | 台账 @0.60 | 阈值已机器化 a9f1513 |
| 4.3 台账与重发布流程 | done | a9f1513 + handoff | — |
| 5.1 演示网页 | done | 19d30f0 | 异常分支未演习（如实） |
| 5.2 ECS 直跑配方 | done | handoff §4 | 子项 O-7 首跑待验 |
| 5.3 交接与全量推送 | done | 2724f02/f585272/c3c1336 | — |

## 剩余触发器（非本 sprint 欠账，按事件闭环）

R5 转正重发布；ECS 首跑冒烟；分诊升级补准确率验收；UI 异常分支补演习；canonical/Hannah 合并复验 O-6；ECS 续费决策（2026-09-12 前）。
