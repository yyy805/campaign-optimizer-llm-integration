---
title: QA 与 E2E 报告 — LLM Integration（as-built）
author: QA
mode: as-built retrospective
status: final
created: 2026-08-14
sources: [tests/fixtures/llm_eval/runs/run-records-2026-08-06.md, TEST-DESIGN(final), CODE-REVIEW, docs/knowledge-base-publications.md]
---

# QA 与 E2E 报告（as-built）

## 1. 范围

整合全部真实运行与离线验收证据，给出"系统端到端是否如声明工作"的 QA 结论。不含：可观测性（非目标）、ECS 首跑（O-7）、KB API 运行时接入（未引入）。

## 2. E2E 场景与结果

| 场景 | 路径 | 结果 | 证据 |
|---|---|---|---|
| 全管道解释（initial_render） | 用户→网页→写手→质检→回答 | OK：写手×2（首轮类型错被修复循环消化）+质检 PASS（答案原文不入库，验收当时目视合规） | run-records R5 |
| 复合问句硬路由 | 用户→门卫 | REFUSED，0 调用，固定文案 | run-records R4 |
| 单意图问句分诊弃权 | 用户→门卫→分诊 | TRIAGE_ABSTAIN → 安全 FALLBACK | run-records R4 |
| 单意图问句意图越界 | 用户→门卫→分诊 | TRIAGE_INTENT_NOT_ALLOWED → 安全 FALLBACK（两次） | run-records R4 |
| 质检员业务判断 | 冻结考卷 8 题 × v9 | 第一轮 8/8；确认轮 7/7+1 NETWORK（非判断）；补证 1/1；零判断错误 | run-records R1–R3 |
| 检索 fidelity | 百炼库 @0.60 五硬门 | 全过（查 R5 只返回 R5；PENDING/RETIRED surfaced；探针无结果） | 台账 |
| 离线回归 | pytest tests/ | 571 通过/1 跳过（含本轮新增守护测试） | c3c1336 |

## 3. 安全行为专项（QA 视角）

- 零消耗真实性：零 provider 桩 + CONFIG 诊断（缺变量即 0 tokens）双证。
- 不回显：伪造身份/verdict/工具字段均被拒且无回显（convergence 伪造桩）。
- 预算：账本封顶真实生效（dry 预算兼容参数化 + 活跑 ≤ 声明上限）。
- 路由：硬拒先于分诊；分诊失败/低置信/越界全部安全回退。

## 4. QA 缺口（如实，与测试设计 §9 一致）

- 分诊分类准确率未验（仅 fail-closed 行为）；flash 误路由已知，UI 主路径兜底。
- FR24 异常分支未演习（渲染级覆盖）。
- FR25 ECS 首跑待验（O-7，首跑即首验）。
- O-6 工作区根校验红（待 canonical/Hannah 合并复验）。

## 5. 未来 QA 触发器

- R5 转正 → 重发布流程 + 考卷/检索复验（流程已定）。
- ECS 首跑 → 冒烟清单（侧边栏身份 + dry-run 零调用 + release-pin 自校验，bundle 根）。
- 任何提示词/ schema 变更 → 升版 + 冻结考卷复验（盖章机制强制）。
- 分诊模型/提示词升级 → 补分类准确率活验收（当前仅 fail-closed 行为验收）。
- UI 异常分支 → 补演习测试（当前渲染级覆盖）。
- canonical/Hannah 合并 → O-6 复验至 MANIFEST_OK。

## 6. QA 结论

在声明范围内，系统端到端行为与 PRD/架构脊声明一致；所有概率组件输出均经确定性门禁；已知缺口全部 fail-closed 兜底并如实记录。**有条件通过**，条件 = §4 四项按 §5 触发器闭环。
