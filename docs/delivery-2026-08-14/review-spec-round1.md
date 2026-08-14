# 技术规范终审报告 — Round 1

- 被审文档：`TECHNICAL-SPECIFICATION.md`（大模型接入 / Campaign Optimizer 解释员，2026-08-14，v1.0）
- 核对输入：参照格式 `technical-specification(1).md`、`ARCHITECTURE-SPINE.md`（AD-1..11、Stack）、`QA-E2E-REPORT.md`、`docs/handoff-ontology-team-2026-08-06.md`
- 审查模式：只读核对，未改被审文档
- 日期：2026-08-14

## Verdict

**PASS（有条件）— 1 medium / 2 low，无 high。** 结构、数字、部署三大面整体一致；medium 项为一处"待验事项写成已验"的措辞，一句话可修。

## 核对点结果

### 1. 12 节结构 vs 参照格式 — PASS

12 个一级节齐全、顺序与参照一一对应；节名适配合理：

| # | 参照 | 被审文档 | 判定 |
|---|---|---|---|
| 1 | 系统概述 | 系统概述 | ✓ |
| 2 | 技术栈（2.1 后端/2.2 前端/2.3 部署环境） | 技术栈（2.1 后端/2.2 模型与检索层/2.3 部署环境） | ✓ 适配（本项目无前端构建层） |
| 3 | 系统架构（3.1/3.2） | 系统架构（3.1/3.2 + 3.3 架构不变式） | ✓ 新增子节不破坏顺序 |
| 4 | 配置管理 | 配置管理（+4.3 发布钉死） | ✓ |
| 5 | 部署规范（5.1–5.4） | 部署规范（5.1–5.4 + 5.5 冒烟清单） | ✓ |
| 6 | 文件处理规范 | 知识与数据处理规范 | ✓ 适配 |
| 7–10 | 安全/开发/监控维护/性能 | 同名 | ✓ |
| 11 | 兼容性说明（2 子节） | 兼容性说明（3 子节） | ✓ |
| 12 | 故障排除（12.1/12.2） | 故障排除（12.1/12.2） | ✓ |

### 2. 数字 / 版本 / 公式 — PASS（1 处限定语缺失，见 F-02）

逐项核对一致：

- Python 3.14、Streamlit 1.61.x（lock 1.61.1）、pytest 9.x、jsonschema（契约主体 Draft-07 / 决策信封 Draft 2020-12）——与脊 Stack 表逐行一致 ✓
- 571 通过/1 跳过（§2.1、§8.2）——与 QA 报告 §2（c3c1336）一致 ✓
- 阈值 0.60 及"机器化进导出清单"——与脊 AD-3（manifest `similarity_threshold`）、`export_knowledge_base_v1.py` 实际字段、台账行一致 ✓
- 预算公式 `max_provider_calls = 4*(rounds+1)+triage`、评测 ledger = 2×案例数、每候选每角色 ≤2 / 门卫 ≤1——与脊 AD-7 一致 ✓（脊写 `max_provider_calls_v12` 且含"BudgetLedger 默认 25"，被审文档省略版本标识与默认值，属细节省略非不一致，见 INFO-2）
- AD 编号 1–11 全数列出，逐条摘要与脊相符（含 AD-3 校验根=冻结 bundle、多匹配熔断；AD-9 硬路由先于门卫；AD-10 ECS 直跑；AD-11 编排线豁免）✓
- 考卷 8 / 路由 50 / 检索 12 / plan_a 夹具——与 `presentation-llm-integration-story.md`（判断考卷 8 题 + 路由卷 50 题 + 检索卷 12 题）及 QA 报告（8 题 × v9）一致 ✓
- 冒烟身份 `R5@2.0-campaign-pending` + `reviewer_v9`——与 handoff §4.7、QA §5 一致 ✓
- 模型名 qwen3.6-flash / qwen3.7-max / qwen3.7-plus、text-embedding-v4 + qwen3-rerank——与脊一致 ✓
- §10.2 temperature=0 / stream=False / enable_thinking=False——在 `three_role_runner_v13.py` 代码中落地 ✓
- "45 次 / 15 万 tokens"：数字与 PRD NFR-2、Addendum A3 一致，但限定语缺失 → **F-02**

### 3. 部署规范 vs handoff §4 — PASS（1 处措辞微差，见 INFO-1）

§5.2 六步与 handoff §4 逐步对应：SSH+Python 3.14+uv ✓；git clone / git archive+scp ✓；`uv sync --frozen`+阿里云索引 ✓；systemd EnvironmentFile 外置 ✓；systemd+nginx+安全组 8501 ✓；冒烟三件套与 handoff §4.7 逐字一致 ✓。"Docker 不用（老师确认）、Dockerfile 保留为备选交付物"与 handoff §1/§3、脊 AD-10 一致 ✓。

### 4. 愿望写成事实检查 — 1 处（F-01）

- O-6（工作区根校验红）：§4.3、§9.3、§12.1 三处均如实标注"待复验/待合并复验至 MANIFEST_OK"，并正确说明运行时走 bundle 根 ✓
- O-7（ECS 首跑）：§9.3 如实列为触发器、§5 为规范性步骤 ✓；但 §11.1 断言"已确认可行" → **F-01**
- 预算数字自报口径限定语缺失 → **F-02**（同类问题）

## Findings

### F-01 [medium] §11.1 把未验事项写成已验

**位置**：§11.1 "ECS Ubuntu 22.04（上海）跨区调北京百炼，**已确认可行**"。
**问题**：QA 报告 §1/§4 明确将 ECS 首跑列为未验（"FR25 ECS 首跑待验（O-7，首跑即首验）"），四份核对输入中无任何跨区连通证据；唯一呼应来自平行文档 `docs/technical-specification-ontology.md` §11 的同款断言（同样无证据指针），属两份文档互相引用、无源可溯。
**建议**：改为"预期可行，以 ECS 首跑（O-7，首跑即首验）为准"，或补证据指针。

### F-02 [low] §10.1 自报成本数字缺限定语（旧账复发）

**位置**：§10.1 "全周期约 45 次/约 15 万 tokens"。
**问题**：该数字的权威出处是 PRD NFR-2 与 Addendum A3，两处均带限定语"（自报口径，无逐次台账）"；PRD round-2 评审 F-08/F-17 曾专门点名同一数字缺限定语。本规范照抄数字但丢掉限定语，等于把自报数呈现为实测数。
**建议**：补回"（自报口径，无逐次台账）"。

### F-03 [low] §4.2 "提示词谱系 v6→v9"表述过窄

**位置**：§4.2 "提示词谱系 v6→v9 逐版继承，不原地编辑"。
**问题**：v6→v9 仅为 reviewer 提示词谱系；脊写 runner 谱系 v6–v13，presentation 记录 executor_v4、triage_v2 各有版本线。现文读作全部提示词都是 v6→v9，与源不符。
**建议**：改为"reviewer 提示词谱系 v6→v9（runner 代码族 v6–v13）逐版继承"或泛化为"各角色提示词/runner 逐版继承"。

## INFO（不构成 findings）

- INFO-1：handoff §4.6 称 nginx 反代为"（可选）"，被审文档 §2.3/§5.2 按标准配置呈现、未标"可选"。参照格式要求有 Nginx 一节，可接受；如求精确可加"可选"字样。
- INFO-2：预算公式省略了脊中的 `_v12` 版本标识与"BudgetLedger 默认 25"；QA §5 的"提示词/schema 变更→升版复验"触发器未进 §9.3 清单，但已被 §4.2 版本盖章规则覆盖。均为细节省略，非不一致。

## Severity 计数

high 0 ／ medium 1 ／ low 2 ／ info 2
