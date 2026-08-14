# Review — TEST-DESIGN.md（LLM Integration as-built 测试设计）· Round 1

- 审查对象：`D:\AAA Data science and AI for busines\AI-projects\bmad\_bmad-output\planning-artifacts\test-design-llm-integration-2026-08-14\TEST-DESIGN.md`
- 核对输入：`tests/` 全部测试文件、`_bmad-output\planning-artifacts\epics.md`、`_bmad-output\planning-artifacts\prds\prd-ai-workflow-lab-2026-08-06\prd.md`、`tests\fixtures\llm_eval\runs\run-records-2026-08-06.md`、`docs\knowledge-base-publications.md`、ARCHITECTURE-SPINE.md、git 历史、pytest 实跑
- 审查方式：只读 + 实跑复验，不改被审文档
- 日期：2026-08-14

## Verdict

**REVISE（基本可信，须修订后定稿）**。追溯矩阵 28 行全部存在、13 个被引 commit 全部真实、"自证监考"机制在代码里确有实体；但出口标准头条数字（587 通过）与实跑结果（570 通过）不符，且存在 1 处幽灵测试文件引用与 1 处幽灵断言引用，第 9 节漏掉 O-6 这条已知活缺口。

## Severity 计数

| 级别 | 数量 |
|---|---|
| High | 2 |
| Medium | 2 |
| Low | 3 |

---

## High

### H1 — 出口标准数字 "587 通过/1 跳过" 与仓库实跑不符（差 17）
- **位置**：§2 三层策略表 L1 行、§8 出口标准第 1 条
- **问题**：文档两处写 "L1 587 通过/1 跳过"（隐含 588 收集）。2026-08-14 在本仓库实跑 `uv run python -m pytest tests -q` 结果为 **570 passed, 1 skipped（571 collected）**，全套绿、零失败，但数字对不上，差 17 项。
- **证据**：
  1. 实跑输出：`570 passed, 1 skipped in 16.73s`（2026-08-14，本文审查时执行）。
  2. `git diff cd0030c HEAD --stat -- tests/`：自 2026-08-06 运行记录入库以来 tests/ 仅增加 1 行断言（test_kb_export_v1.py）与文档修订，**没有删过任何测试**——即 08-06 当天 main 上也就是 ~570/571，588 这个数在本分支从未成立过。
  3. 该数字沿袭自 PRD M-4（"588 项收集（587 passed / 1 skipped）"）与 presentation 文档，TEST-DESIGN 作为 2026-08-14 新写的 as-built 文档未复验即照抄。587 的来源无法在本分支复现（疑似取自未合并分支或过时记忆）。
- **建议**：把 §2/§8 的数字改为当前实跑口径（570 通过/1 跳过/571 收集，注明复验命令与日期），并加一句 caveat 说明 PRD 时代 588 口径与本分支当前收集数的差异及原因（若查得来源分支则注明）。同时建议回头修订 PRD M-4 的同一数字，保持单一事实源。

### H2 — FR8 行引用了不存在的测试文件 test_three_role_runner_v13.py
- **位置**：§5 追溯矩阵 FR8 行（"test_three_role_runner_v12/v13；run-records R4"）
- **问题**：`tests/` 下不存在 `test_three_role_runner_v13.py`（全仓库 glob 仅命中源码模块 `campaign_optimizer\llm\three_role_runner_v13.py`）。这是"引用了不存在的测试"。
- **证据**：tests/ 目录实际文件清单中 runner 测试止于 v12（另有 test_three_role_e2e_v15.py）；v13 的行为实际由其他文件覆盖：`test_llm_convergence_integration.py`（`ThreeRoleRunnerV13` dry-run + `RoleCallAdapterV13` 伪造桩用例）与 `test_reviewer_v13.py`（12 个测试，含 `BudgetLedgerV12` 接线）。
- **建议**：FR8 行改写为真实覆盖点：`test_three_role_runner_v12.py`（预算公式/BudgetExceeded）+ `test_llm_convergence_integration.py`（v13 dry-run 与伪造工具桩）+ `test_reviewer_v13.py`（通道校验）+ run-records R4。若确有意为 v13 runner 单列测试文件，那是缺口而非既成事实，应移入 §9。

---

## Medium

### M1 — FR24 "失败安全类别" 被记为有测试覆盖，实际无任何断言触发该分支
- **位置**：§5 追溯矩阵 FR24 行（"test_streamlit_ui（失败安全类别）"）
- **问题**：`tests\test_streamlit_ui.py` 全部只有一个测试函数 `test_ui_renders_and_dry_run_is_safe`，内容为：渲染 app、点一次按钮、断言无异常且出现"零调用"提示。**没有任何用例注入异常去触发 app.py 第 80 行的 `except Exception →` 安全类别分支**，"失败只显示安全类别"这一条在离线侧无断言。
- **证据**：test_streamlit_ui.py 全文 16 行（已通读）；app.py 的失败分支存在（功能真实），但无测试覆盖；`grep "def test_"` 于该文件仅 1 命中。
- **建议**：二选一：(a) 补一个 AppTest 用例（如用 classifier/provider 工厂注入异常，断言只出现安全类别文案、不出现细节）；(b) 若暂不补，把 FR24 行改为"app.py 异常分支（人工演示验证，离线断言缺口）"并在 §9 记一笔。当前写法属于"FR 无测试覆盖却标了覆盖"。

### M2 — §9 已知缺口漏掉 O-6（main 工作区 manifest 根校验当前抛 PackageDriftError）
- **位置**：§9 已知缺口
- **问题**：PRD §9 O-6 与 ARCHITECTURE-SPINE Deferred 都明确记着：main 工作区对 `publication_manifest.json`（626cfbde…）的根级校验**当前抛 PackageDriftError**（合并顺序漂移），依赖 canonical/Hannah 分支合并后重跑至 MANIFEST_OK；spine AD-3 也注明这是独立合并健康检查、非运行时门禁。这是仓库里唯一一条"已知活着的失败校验"，且 §5 的 FR3/FR21 行恰是关于 PackageDrift/发布校验的——测试设计文档的缺口节对此只字未提，读者会误以为所有门禁全绿。
- **证据**：PRD §9 O-6；ARCHITECTURE-SPINE.md Deferred 第 3 条与 AD-3；TEST-DESIGN §9 三条缺口（triage 路由质量、FR25/O-7、KB API、可观测性）均不含 O-6。
- **建议**：§9 增补一条："O-6 工作区根级 manifest 校验当前为 PackageDriftError（合并顺序漂移，运行时经 bundle 级校验安全）；待 canonical/Hannah 合并 + 文件对齐后重跑至 MANIFEST_OK，当前测试套件只钉 bundle 级校验"。另两条 spine Deferred（R5 转正重发布触发、ECS 续费决策）属事件/商务性质，可一句带过或显式说明"非测试缺口"。

---

## Low

### L1 — FR22 侧边栏三要素只有"渲染不崩"级覆盖，无内容断言，文档未区分
- **位置**：§5 追溯矩阵 FR22 行（"test_streamlit_ui"）
- **问题**：app.py 侧边栏确有发布身份（rule_version）/Reviewer 提示词版本/凭据存在性三项（第 54–59 行），AppTest 渲染会执行到这段代码、崩了能抓住；但**没有任何断言检查这三项的值或"永不显示值"**。FR22 行未加限定语，容易被读成内容级覆盖。
- **证据**：test_streamlit_ui.py 仅断言 `not at.exception` 与"零调用"文案；app.py 侧边栏实现已核对存在。
- **建议**：FR22 行加注"渲染级覆盖；侧边栏取值与'永不显示值'无内容断言"，或补两条 AppTest 断言（侧边栏含 rule_version/提示词版本、不含密钥值）。

### L2 — FR20 证据栏写"台账 + 截图"，截图不在仓库内
- **位置**：§5 追溯矩阵 FR20 行（证据："台账 + 截图"）
- **问题**：`docs\knowledge-base-publications.md` 台账确实记录了 @0.60 五门全过及召回百分比（R5-only 74/72/69% 等），仓库内可验；但"截图"未入库——PRD §7 自己写明"召回百分比入台账（截图未入库）"。引用不在库的证据削弱可复核性。
- **证据**：knowledge-base-publications.md "Retrieval acceptance (S3)" 节；PRD §7 验收表对应行。
- **建议**：证据栏改为"台账（含召回百分比）；原始截图未入库（见 PRD §7）"。

### L3 — NFR2 证据中的"逐轮批准记录"不是仓库内工件
- **位置**：§5 NFR 横切行（"NFR2→预算测试+逐轮批准记录"）
- **问题**：预算测试真实存在（test_budget 参数化 5 组、BudgetExceeded 断言、dry 预算兼容）；但"逐轮批准记录"是会话过程事实，仓库内无对应工件，属自报口径——PRD NFR-2 已诚实标注"自报口径，无逐次台账"，TEST-DESIGN 未带此限定。
- **证据**：PRD §6 NFR-2；tests\test_three_role_runner_v12.py 第 72、75 行。
- **建议**：改为"预算测试 + 逐轮批准（过程事实，自报口径，无仓库内台账）"。

---

## 通过项（抽查证据，≥12 行矩阵 + 4 commit）

**追溯矩阵抽查（16 行）**：FR1（schemas 13/negative_matrix 12/edge_cases 8 个测试，均实存）；FR2（IDENTITY_FIELDS 恰 6 字段，`test_each_release_identity_mutation_fails_before_provider_construction` 参数化 + 不回显断言，实存）；FR3（`PackageDriftError` 断言实存，test_ontology_release_pin.py:103）；FR4（`INACTIVE_RULE` 在 convergence 与 local_rule_retriever 各实存）；FR5（verdict 矛盾 fail-closed + 不回显，实存）；FR6（两文件实存 + run-records R5 在库）；FR9（`test_budget` 公式 4*(rounds+1)+triage 五组参数化、`pytest.raises(BudgetExceeded)`、convergence dry 预算兼容，均实存）；FR11（六种变异 count/duplicate_id/broken_candidate_ref/rule_field_claim/verdict_tamper/label_mismatch 与文档描述逐一对应）；FR12（pending 链绑定 canonical 身份 + 空规则上下文，实存）；FR13（诊断行断言 `"answer" not in serialized` 且 `"SECRET" not in`，实存）；FR15/17（routing-safety.json 恰 50 案例，70=50+20 标签策略测试实存）；FR18/19/21（双跑一致、忠实投影、`similarity_threshold == 0.6` 断言均实存）；FR23（"零调用"断言对应 dry 默认）；FR25/26/27（handoff 文档在库、工作树 tracked 干净且与 origin/main 同步，实验核对）。

**commit 抽查（13 个，全部真实）**：38fcc6d、de07eb4、c971b0b、4fee4e3、54c2e4e、40002ba、a9f1513（show --stat 核实：阈值机械钉死 + 守卫测试）、80fe8f6（show --stat 核实：orchestrator + 329 行测试同提交入库）、eeca02a、19d30f0、2724f02、459c97f、f585272 均在 git log 中，主题与文档用途相符。零幽灵 commit。

**策略自洽**："自证监考"不是口号——`ProviderMustNotBeConstructed`（构造 provider 即 AssertionError）、`ForgedToolClient`（伪造 verdict/身份 → 拒且不回显）、六种考卷变异全拒、negative_matrix 均在代码中实存；L2 彩排有 subprocess/dry 断言（test_reviewer_judgment_dataset 的 zero-provider 链、test_three_role_e2e_v15 的 dry 预算两例）；L3 数字与 run-records 一致（8/8、7/7+1 NETWORK、补证 1/1、零判断错误；检索五门 @0.60；路由 R4 REFUSED/ABSTAIN/NOT_ALLOWED）。NFR4（双跑一致）与 NFR5（DENIED_DATA_MARKERS 入验证器 + kb 导出 marker 断言）均有真实断言。§7 环境约定（`uv run python -m pytest`、CONFIG/0 tokens = 缺变量）与实跑经验一致。

## 修订优先级建议

1. H1（改数字 + caveat）与 H2（改 FR8 引用）为定稿前必改。
2. M1、M2 同批处理（一个补测试或降级标注，一个补 §9）。
3. L1–L3 为措辞级，顺手改掉。
