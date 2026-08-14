---
review: adversarial architecture review — round 1
target: ARCHITECTURE-SPINE.md（LLM Integration / Campaign Optimizer Explainer, 2026-08-13 draft）
reviewer-lenses: [现实核对, 对抗构造, 完整性]
date: 2026-08-13
verdict: conditional — 需一轮 AD 修订后方可作为权威基线
severity-counts: {high: 3, medium: 4, low: 4}
---

# Review — ARCHITECTURE-SPINE.md（Round 1）

## Verdict

**有条件通过（Conditional）**。脊的整体 as-built 可信度高：15+ 处现实抽查（Python 3.14、pytest 9.1.1、Streamlit lock 1.61.1、模型别名三件套、六字段身份、预算公式、`schema_version: "1.0"`、R5 状态、KB 台账、8 案例考卷、50 题路由集、app.py 侧边栏三要素、tracked 树干净）全部属实。但存在 3 个 High 级对抗洞：**逐字遵守全部 AD 的两个实现者仍会建出不兼容系统**（校验根未定、Reviewer 决策形状两个主人、FR28 编排线无治理且与自定规范矛盾）。修复这些需要新 AD 或收紧既有 AD 的 Rule 文本，不是措辞润色。

## 现实核对：已证实（抽样 ≥15，全部通过）

| 脊的声称 | 证据 | 结论 |
| --- | --- | --- |
| Python 3.14 / pytest 9.x | `.python-version`=3.14；pyproject `pytest>=9.1.1` | ✓ |
| qwen3.6-flash / qwen3.7-max / qwen3.7-plus | `agent_roles.v15.json` model_aliases 逐字匹配 | ✓ |
| 六字段发布身份 | `release_pin.py` IDENTITY_FIELDS 逐字匹配 | ✓ |
| 预算公式 4*(rounds+1)+triage、triage≤1、BUDGET_DENIED | `agent_workflow_v12.py` max_provider_calls_v12 / BudgetLedgerV12 | ✓ |
| 契约 JSON schema_version "1.0" | 全部 11 份 schema 均 const "1.0" | ✓ |
| R5@2.0-campaign-pending = PENDING_HUMAN_REVIEW | `ontology/rules/R5.json` | ✓ |
| KB eeirxr7djz / text-embedding-v4 / qwen3-rerank / 阈值 0.60 | `docs/knowledge-base-publications.md` 台账行 | ✓ |
| 考卷 8 案例 / 路由集 50 题 / 检索卷 12 题 | fixtures 目录实数相符 | ✓ |
| runner 谱系 v6–v13、agent_roles.v5–v15 | 文件实际存在 | ✓ |
| app.py：侧边栏三项、dry-run 默认勾选、失败只显安全类别 | `app.py` 逐行核实 | ✓ |
| release-pin 自校验（bundle 级）通过 | 实测 `load_verified_manifests()` 返回 R5@2.0 + R5@1.3 两份，均验证通过 | ✓ |
| O-6 工作区漂移 | 实测工作区级验证抛 PackageDriftError，**71 条 entry 漂移**（hannah_* 全 MISSING + contracts/ontology 哈希不符）——漂移为真实现状 | ✓（但引出 H-1） |
| tracked 树干净（FR27） | git status：仅 10 个未跟踪工件，tracked 无改动 | ✓ |

---

## Findings

### HIGH

#### H-1｜AD-3 校验根未定：同一棵树，两个合规实现者得出相反 go/no-go（对抗）
- **位置**：AD-3 Rule；Structural Seed 之上缺一条裁决
- **问题**：Rule 只说"六字段身份 + package_checksum 在构造 provider 前校验，漂移即错误"，从未指定**对哪个根校验**。实测：对冻结 bundle（`.ontology_bundles/<source_commit>/`）校验 → **通过**（app.py 现状）；对工作区/部署树校验 → **失败**（71 条 entry 漂移，O-6 现状）。Builder A 用 bundle 根、Builder B 用工作区根，两人都逐字遵守 AD-3，冒烟结论相反；ECS 上 `git clone` 的树做工作区级校验**必然红**。
- **证据**：实测探针两次运行结果；`release_pin.py` `load_verified_manifests(root=...)` 的 root 参数即此二义性所在；PRD §9 O-6 仅口头补了"运行时经 bundle 级校验安全"，脊未吸收。
- **建议**：收紧 AD-3 Rule：校验根 = manifest `source_commit` 指名的冻结 bundle 目录；工作区级漂移是独立的受跟踪状态（O-6，待 MANIFEST_OK），不作为运行时门禁；AD-10 冒烟项注明断言的是 bundle 根。

#### H-2｜Reviewer 决策形状有两个主人，只有一个被哈希钉死（对抗）
- **位置**：AD-4（不可变工件清单）、AD-5（本地权威 schema）
- **问题**：同一个 Reviewer 决策信封有两份定义：`llm/tools/submit_reviewer_decision_v1.schema.json`（经 agent_roles 配置哈希钉死，管 Function-Calling 线上通道）与 `schemas/reviewer_decision_v3.schema.json`（`agent_workflow_v5.py:126`、`reviewer_diagnostic_v10.py:33` 运行时以 Draft202012Validator 校验，**不在 AD-4 钉死清单内**）。Builder A 演进未钉死的那份（合规——AD-4 没管它），Builder B 只认工具 schema → 同一决策，两条门禁尺度。
- **证据**：grep 证实两文件并存且分别被引用；AD-4 枚举为"提示词、工具 schema、考卷、导出"，不含 `schemas/` 下的决策模板。
- **建议**：AD-4 扩列 `reviewer_decision_v3.schema.json`（及 triage 对应件）入哈希钉死；或明文规定它是工具 schema 的投影、单一所有权归 `llm/tools/`，并加一致性测试。

#### H-3｜FR28 编排线零治理，且与 Consistency Conventions 直接矛盾（对抗 + 完整性）
- **位置**：frontmatter `binds: [FR1-FR28]` vs Capability Map（无 FR28 行）；Consistency Conventions "状态"行
- **问题**：规范说"**无共享可变状态**；每请求组装上下文；账本为进程内对象"。但 FR28 要求 `SessionStore` 做租户/用户级会话隔离——`orchestrator.py` 的 `InMemorySessionStore` 正是跨请求共享可变状态（进程内对象 ≠ 无共享可变状态）。Builder A 遵守规范 → 做无状态编排器，FR28 验收失败；Builder B 实现 SessionStore → 字面违反规范。且整条 local-first 线没有任何 AD 管辖：它与三角色主管道的门禁等价性、chat 入口身份（AD-9 只 bind "chat 入口"，未点名 orchestrator 也是 chat 入口）、in-memory 重启即失忆的限制，全部沉默。
- **证据**：`orchestrator.py:16,43`（InMemorySessionStore 默认）；Capability Map 六行无 FR28；epics Story 2.5 有验收标准但脊未映射。
- **建议**：新增 AD-11（本地优先编排线）：门禁与主管道等价（实测 `resolve_chat` 已复用 HybridIntentPolicy，写明）、SessionStore 为规范的显式豁免项及其边界（进程内、非持久、demo 级隔离）、FR28 进 Capability Map。

### MEDIUM

#### M-1｜Stack 表 "jsonschema | Draft 2020-12" 是愿望式概括（现实核对）
- **位置**：Stack 表
- **问题**：11 份 schema 中 9 份声明 `draft-07`，仅 `reviewer_decision_v3` / `triage_decision_v2` 两份声明 Draft 2020-12（代码对这两份显式调用 Draft202012Validator）。as-built 脊把局部事实写成全局事实。
- **建议**：改为"契约主体 Draft 07；决策信封（reviewer/triage）Draft 2020-12"，与代码一致。

#### M-2｜多份已验证 manifest 共存时的选择策略未定（对抗）
- **位置**：AD-3；`app.py` manifest 选取处
- **问题**：`load_verified_manifests()` 今天已返回两份（current R5@2.0 + history R5@1.3）。app.py 用 `next(v for v in ... if ontology_version == "2.0-campaign-pending")`——依赖 dict 插入顺序的首个匹配。R5 转正重发布后同一 ontology_version 下出现新 rule_version，或 history 再添同 ontology_version 条目时，Builder A 取首匹配、Builder B 取最高 rule_version → 侧边栏身份与投影原文不同。
- **证据**：实测返回两份 manifest；`history/manifests/` 目录设计即预期多份共存。
- **建议**：AD-3 增补选择规则：按表面（surface）显式钉死 `rule_version`，或"匹配必须唯一，多匹配即 fail-closed"，禁止首匹配语义。

#### M-3｜阈值 0.60 机械层面未钉死，只活在散文里（对抗 + 完整性）
- **位置**：AD-3 "相似度阈值 0.60 随发布配置走"
- **问题**：`kb_export/v1/manifest.json` **无** similarity threshold 字段；0.60 仅存在于 `docs/knowledge-base-publications.md` 台账散文与 handoff §2.3。Builder A 按机器可读的导出 manifest 写重发布流水线（合规——脊没说阈值在哪）→ 落回平台默认 0.20 → 污染与探针硬门失败——这正是台账里记录过的真实失败模式。
- **建议**：阈值进 `kb_export/v1/manifest.json`（或独立发布配置结构），重发布校验器强制读取；AD-3 指明其机械位置。

#### M-4｜ECS 租期 2026-09-12 续费决策未进 Deferred（完整性）
- **位置**：Deferred 清单
- **问题**：PRD O-3 明示机器租期至 2026-09-12、需续费决策（距脊创建日仅 30 天）。AD-10 把部署力学写全了，但整个成本/生命周期维度在脊里沉默；Deferred 五项无一提及。对以"可交接"为 NFR6 的脊，这是会咬人的已知时限。
- **建议**：Deferred 增补"ECS 续费决策（2026-09-12 前），理由：部署配方已可交接，租约属商务事件非架构事件"；或并入 AD-10 的运维边界说明。

### LOW

#### L-1｜Structural Seed 低估了 as-built 树（现实核对）
- **位置**：Structural Seed
- **问题**：`ontology/` 实际还有 assertions/、guardrails/、history/、mta/、policies/、clients/、schemas/、db.py、publication.py、condition_evaluator.py；`contracts/` 还有 concept_authority.py、feedback.py；`schemas/` 共 11 份而非"五份契约模板"（五份是 FR1 信封子集）；`campaign_optimizer/inference/` 整包未提。Seed 可简化，但 as-built 脊应标注"节选"或补全，否则照 seed 重建者会漏掉发布校验实际覆盖的资产。
- **建议**：seed 加一行"此处为最小承载集；完整资产清单以 publication_manifest.json entries 为准"。

#### L-2｜AD-7 "总量账本封顶"无数字（完整性）
- **位置**：AD-7 Rule
- **问题**：封顶公式（4*(rounds+1)+triage；评测 ledger = 2×案例数；BudgetLedgerV12 默认 25）只存在于 epics Additional Requirements，脊未绑定。Builder 只读脊时"封顶"不可执行。
- **建议**：AD-7 Rule 引用公式或声明"以预算公式 max_provider_calls_v12 为准"。

#### L-3｜可观测性维度沉默，未声明为非目标（完整性）
- **位置**：全文
- **问题**：日志/健康检查/运行审计除 systemd stdout 外无约定。feature 高度脊可以不覆盖，但应显式声明为非目标，避免接手方误以为已治理。
- **建议**：Deferred 或脚注一行："运行可观测性（结构化日志/指标）超出本脊高度，留待运维阶段"。

#### L-4｜Streamlit 版本表述（现实核对，微差）
- **位置**：Stack 表
- **问题**：脊写 "1.61"，uv.lock 实际锁 1.61.1。无实质风险。
- **建议**：写 "1.61.x（lock 1.61.1）"。

---

## 通过项（无需动作）

- **Deferred 五项全部给了"为什么能等"**：Triage 升级（已 fail-closed 兜底）、R5 重发布（事件未发生）、canonical/Hannah（团队决策 + O-6 挂钩）、KB API 接入（须先立新 AD）、ECS 首跑（首跑即首验）。✓
- **AD 可执行性**：AD-1/2/4/5/6/8/9/10 的 Rule 均可落到现有代码门禁或验收脚本；AD-2"对外只暴露安全类别"有错误码 allowlist 支撑（`REVIEWER_BINDING.*`、`INACTIVE_RULE`、`SYSTEM_FALLBACK` 等实测存在于代码）。
- **依赖方向图**与代码实际调用方向一致（app.py → ThreeRoleRunnerV13 → contracts/release_pin；KB 仅数据身份入角色层）。
- **命名/盖章规范**与实况一致（`*_vN.py` 逐版继承、考卷带版本后缀、agent_roles.v15 哈希校验经 loader 实测存在）。

## Round 2 建议优先级

1. H-1 + M-2 合并为 AD-3 的一次收紧（校验根 + 选择策略）——成本最低、消两个洞。
2. H-3 新立 AD-11 并修 Consistency Conventions 的状态行。
3. H-2 + M-3 属"钉死清单补全"，一次改 AD-4/AD-3 + 两个 manifest 字段即可。
4. M-1/L-1/L-4 为文本修正，随手带。
