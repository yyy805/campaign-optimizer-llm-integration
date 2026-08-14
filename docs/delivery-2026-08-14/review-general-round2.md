# Cynical Review · Round 2（Blind Hunter / 对抗式复审）

- 被审对象：`prd.md`（大模型接入 Campaign Optimizer 解释员，as-built retrospective）+ `addendum.md`（修订后版本）
- 复审日期：2026-08-06
- 复审范围：(1) 逐条核对上轮 `review-general.md` 的 F-01..F-10 是否如实解决；(2) 检查修复是否引入新矛盾、新"愿望写成事实"声明。
- 审查方式：只读审查 + 现场复现（git 状态、manifest 漂移重跑、pytest collect、全仓库 grep）。未修改被审文档。
- 立场不变：不信任文档自述，只信任仓库可复现证据。

---

## 总 Verdict

**PASS-WITH-FINDINGS（较上轮改善）**

上轮两条 high 均已如实解决；10 条中 7 条完全解决、3 条部分解决、0 条未解决。修复本身**引入了两条新的 medium 问题**：(1) FR-27"全部提交推送 GitHub"当前为假——恰好是承载入库运行记录（§7/M-1 核心证据）的 commit `cd0030c` 尚未推送；(2) O-3/A6 新写的"实例/公网 IP 等基础设施标识符……不入库"与仓库现状直接矛盾——公网 IP 仍留在已提交且已推送的 `docs/presentation-llm-integration-story.md`。其余为低级别的引用悬空、范围清单漏项与措辞不对称。

| severity | 计数（本轮新增） |
|---|---|
| critical | 0 |
| high | 0 |
| medium | 2（F-11、F-12） |
| low | 6（F-13..F-18） |

---

## 一、上轮十条核对表

| 上轮 finding | 状态 | 核对证据 |
|---|---|---|
| F-01 [high] "8/8 ×2"夸大验收 | **已解决** | §3 M-1、§7、FR-14 三处均改为"第一轮 8/8；确认轮 7/7 有效 + 1 NETWORK；补证 1/1"，门定义改为"每题至少两次正确判断"；grep 确认"×2/连续两轮全对"字样只残留于上轮审查报告自身，被审文档中无残留。三处措辞彼此一致，且与入库运行记录 R1–R3、交接文档 §1（"两轮+补证零判断错误"）逐项吻合。 |
| F-02 [high] 工作区漂移未入开放项 | **已解决** | §9 新增 O-6，措辞与建议一致。本审查现场复现仍属实：根级 `verify_publication_manifest()` 对 checksum `626cfbde…` 抛 `PackageDriftError`（exit 1）；`release_pin.load_verified_manifests()` bundle 级校验通过（2 份 manifest）。"运行时安全、工作区漂移"的描述与现状精确相符。 |
| F-03 [medium] 真实运行记录未入库 | **已解决**（残留见 F-11/F-18） | `tests/fixtures/llm_eval/runs/run-records-2026-08-06.md` 已入库（commit `cd0030c`，git ls-files 确认 tracked），§3/§7/FR-14 均改为引用它。R4 含 2 条真实 TRIAGE_INTENT_NOT_ALLOWED 记录，直接回应了上轮对 NOT_ALLOWED 的具体质疑。残留：该 commit 尚未推送（F-11）；文件为脱敏重构版（F-18，PRD 已如实标注"重构版"）。 |
| F-04 [medium] orchestrator/session_store 未写入 FR | **已解决** | 新增 FR-28（§5 F8 节），四条证据路径全部存在：`campaign_optimizer/llm/orchestrator.py`（docstring "Backend-routed orchestrator that never returns unguarded model text"）、`session_store.py`（`SessionContext` tenant/user/session 三字段隔离）、`tests/test_local_llm_orchestrator.py`（本轮 pytest collect 中可见）、`docs/architecture/llm-integration-local-first.md`。定位表述（并行交付、demo 主路径走三角色 runner）与事实一致。 |
| F-05 [medium] 镜像未构建验证未入开放项 | **已解决** | §9 新增 O-7，与建议逐字一致："Dockerfile 未经过真实构建验证（本地引擎不可用，待 ECS 首构）；首次构建风险见交接文档部署清单。"与 FR-25（只声称文件内容）无矛盾。 |
| F-06 [low] runner 谱系 v5 不存在 | **已解决** | Addendum A1 改为"基座 `three_role_runner.py` + `v6–v13`（逐版继承；谱系自 v5 工作流契约演化）"。glob 核实：主树存在 `three_role_runner.py` + v6..v13 共 9 个文件，无 v5 runner（`agent_workflow_v5.py` 是工作流契约，非 runner）。 |
| F-07 [low] FR-27"工作区干净"需限定 | **部分解决** | 措辞已改为"tracked 树干净（验收/进度类未入库工件另见 §9 与 F-07 注）"。`git status --porcelain` 核实 tracked 树确实干净 ✓。但两个残留：(a) 同句的"全部提交推送 GitHub"当前为假（见 F-11）；(b) "见 §9 与 F-07 注"是悬空引用（见 F-13）。 |
| F-08 [low] 成本数字自报口径 | **部分解决** | NFR-2 已加"（自报口径，无逐次台账）"✓。但 Addendum A3 同一数字（"全周期真实调用约 45 次、约 15 万 tokens"）仍未加限定语（见 F-17）。 |
| F-09 [low] 基础设施标识符入库 | **部分解决** | PRD O-3 与 Addendum A6 已移除实例 ID 与公网 IP，改为"存内部台账，不入库"。实例 ID 全仓库 grep 已无残留 ✓。**但公网 IP `47.102.115.250` 仍留在 `docs/presentation-llm-integration-story.md:114`，且该文件已提交（9c99aee）并已推送（origin/main 包含该 commit）**——新写的"不入库"声明与仓库现状矛盾（见 F-12）。 |
| F-10 [low] 587/588 口径不一致 | **已解决** | M-4 改为"588 项收集（587 passed / 1 skipped），零真实调用"；§7 同步"588 收集：587 passed / 1 skipped"。本轮 `pytest --collect-only` 实测 **588 tests collected**，与两处口径一致。 |

**小结**：7 已解决 / 3 部分解决 / 0 未解决。两条 high 的修复实质、如实、可验证。

---

## 二、本轮新 Findings

### F-11 [severity: medium] 位置：FR-27、§7 证据链
**问题**："全部提交推送 GitHub"当前为假——承载核心验收证据的 commit 未推送。
**证据（本轮现场核查）**：
- `git log origin/main..HEAD` = `cd0030c docs(eval): add sanitized reconstructed real-run records`，恰是 F-03 修复（运行记录入库）那个 commit，**尚未推送**。
- §7 验收表中 Reviewer 考卷、路由三层、E2E 三行的证据都指向该 commit 引入的文件；§3 M-1 亦同。任何基于 GitHub 的第三方此刻看不到这份证据。
- 附带事实：`.gitignore:49` 忽略 `_bmad-output/`，PRD 本体本就不入仓库——这不违反任何 PRD 声明，但说明"可追溯性"完全依赖仓库内证据，而该证据的推送恰恰滞后。
**定性**：不是捏造（文件在本地仓库真实存在、可复验），但 FR-27 的"全部提交推送 GitHub；证据：2724f02 及后续"这句此刻是字面为假的陈述，且卡的正是本轮最重要的证据。
**建议**：推送 `cd0030c` 后复核 `git log origin/main..HEAD` 为空；或把 FR-27 措辞改为"全部提交已推送（除待推送的证据入库 commit `cd0030c`）"。

### F-12 [severity: medium] 位置：§9 O-3、Addendum A6
**问题**：新写的"标识符不入库"声明被仓库自身证伪——公网 IP 仍在已推送的文档里。
**证据**：
- O-3："实例/公网 IP 等基础设施标识符存内部台账，**不入库**"；A6 同措辞。
- 全仓库 grep：`docs/presentation-llm-integration-story.md:114` 仍含 `ECS 实例（47.102.115.250）`；该文件由 commit `9c99aee` 引入，`git branch -r --contains 9c99aee` 确认已在 origin/main（已推送）。
- 实例 ID `i-uf6ctiuazm4gv5cvzyje` 全仓库已无残留（移除成功一半）。
**定性**：修复只清了 PRD/addendum，漏了 presentation 文档，导致"不入库"从一条整改措施变成一条与仓库现状矛盾的声明。IP 非密钥，NFR-5（密钥/客户数据）未被违反，但承诺与现实不符即是回溯文档的病灶。
**建议**：从 presentation 文档删除该 IP（如需保留历史则改写为"ECS 实例（标识符见内部台账）"）；在仓库可见性为公开的情况下评估是否需要历史清洗；或将 O-3/A6 措辞改为"已从 PRD/addendum 移除；presentation 文档清理待办"。

### F-13 [severity: low] 位置：FR-27 括注
**问题**：悬空且不自明的交叉引用——"（验收/进度类未入库工件另见 §9 与 F-07 注）"。
**证据**："F-07"是上轮审查报告（`review-general.md`）的 finding 编号，PRD 未指明出处，脱离该报告的读者无法解析；且 §9 的 O-1..O-7 中**没有**任何一条列出未入库工件清单，指针落空。本轮 `git status` 实测未入库路径为：`.continue/`、`.tmp_codex_transcript.md`、`.worktrees/`、`docs/architecture-diagram-2026-08-06.html`、`docs/progress-report-2026-08-06.md`、`review-integrity.md`、`review-reality.md`。
**建议**：把括注改为自明表述，如"（验收/进度类未入库工件：progress-report、review 记录、架构图 HTML 等，见 `git status`）"，或直接点名 `review-general.md` F-07。

### F-14 [severity: low] 位置：§4 范围清单 vs §5 F8
**问题**：范围清单漏掉已立项能力——§4"在范围内"枚举（契约门禁/三角色管道/Reviewer 验收/路由安全/知识库/演示网页/镜像配方/交接与台账）不含 local-first 编排线，而 §5 已有 FR-28（F8 节）。§8 里程碑回溯同样未提该线。
**建议**：§4 清单补"local-first 编排线（并行交付）"一项，或在 FR-28 处注明"§4 清单外补充交付"。

### F-15 [severity: low] 位置：§5 节序
**问题**：节序非单调——F8（FR-28）插在 F6 与 F7 之间（F6 演示网页 → F8 编排线 → F7 交付与交接）。FR 编号本身无冲突（FR-25..27 属 F7，FR-28 属 F8），但节级阅读顺序断裂。
**建议**：把 F8 移到 F7 之后，或改编号为 F7-bis；纯排版问题，随下次修订处理。

### F-16 [severity: low] 位置：§3 反指标括注
**问题**："须用连续轮次而非单轮度量（M-1 的设计即为此）"与 M-1 现行门措辞轻微错位：M-1 门现为"每题至少两次正确判断"（逐案例累计，不要求连续）。实际上 8 案例中 1 例（`pending_revise_definitive_verdict`）的第二次正确判断来自补证轮，其间确认轮是 NETWORK（非判断）——该案例的两次正确判断并非"连续轮次"取得。门本身满足（不要求连续），仅反指标括注把 M-1 归因为"连续轮次"度量稍有失真。
**建议**：括注改为"（M-1 的逐案例双次正确门即为此设计）"。

### F-17 [severity: low] 位置：Addendum A3
**问题**：F-08 的限定语只加在 PRD NFR-2，Addendum A3 的同一数字（"全周期真实调用约 45 次、约 15 万 tokens"）仍无限定，两份文档对同一自报数的口径不对称。
**建议**：A3 补"（自报口径，无逐次台账）"，与 NFR-2 对齐。

### F-18 [severity: low/informational] 位置：`tests/fixtures/llm_eval/runs/run-records-2026-08-06.md`
**问题**：入库证据的残余粒度问题（非 PRD 失真，PRD 已如实标注"重构版"）：(a) 文件自述"由会话记录/终端截图重构入库，非原始 JSON 直存"——仓库内最强证据仍是截图转写件；(b) R2 只给"7 案例 | 各自 | 各自匹配"的汇总行，未逐案列出，复验者无法独立确认是哪 7 案例（可推断为除 NETWORK 案例外的全部，但属推断）；(c) R6 历史轮次数字指向仓库外会话记录（已如实标注"归因用"）；(d) §7 E2E 行的"chat 安全回退正确"实由 R4 的 chat 运行支撑，R5 节只含 initial_render——引用整份文件，技术上成立，但对应关系不显式。
**建议**：如后续再修订，把 R2 补成逐案行即可；其余保持现有披露强度已足够诚实。

---

## 三、本轮新证据抽查台账（13 处）

| # | 声明 | 抽查结果 |
|---|---|---|
| 1 | FR-27"全部提交推送 GitHub" | ✗ `git log origin/main..HEAD` = `cd0030c`（未推送）→ F-11 |
| 2 | FR-27"tracked 树干净" | ✓ `git status --porcelain` 仅 `??` 未入库项，无已修改 tracked 文件 |
| 3 | O-6"根级校验抛 PackageDriftError（626cfbde…）" | ✓ 现场复现：`verify_publication_manifest(load_publication_manifest())` 抛 `PackageDriftError: ontology publication manifest does not match package files`，checksum 前缀 `626cfbde` |
| 4 | O-6"运行时经 bundle 级校验安全" | ✓ `release_pin.load_verified_manifests()` 通过，2 份 manifest（626cfbde、b55acbd6） |
| 5 | M-4/§7"588 项收集（587 passed / 1 skipped）" | ✓ `pytest --collect-only` 实测 588 tests collected |
| 6 | O-3/A6"标识符不入库" | ✗ 公网 IP 仍在 `docs/presentation-llm-integration-story.md:114`（已提交并推送）→ F-12；实例 ID 已无残留 ✓ |
| 7 | FR-28 四条证据路径 | ✓ `orchestrator.py`（服务端门禁 docstring 与声明一致）、`session_store.py`（tenant/user/session 隔离）、`test_local_llm_orchestrator.py`（在 588 收集内）、`docs/architecture/llm-integration-local-first.md` 均存在 |
| 8 | A1 runner 谱系"基座 + v6–v13" | ✓ glob 主树 9 个文件吻合，无 v5 runner |
| 9 | F-01 清除彻底性 | ✓ grep"×2/连续两轮全对"仅命中上轮审查报告自身，被审文档零残留 |
| 10 | 交接文档 §1 与 M-1 新口径 | ✓ "冻结测评集验收关闭：两轮+补证零判断错误"——与 §3/§7/FR-14 一致，上轮的跨文档不一致已消除 |
| 11 | run-records R1–R5 ↔ §3/§7/FR-14 数字 | ✓ 逐项吻合：8/8、7/7+1 NETWORK（0 tokens）、补证 1/1、路由 4 运行（REFUSED 0 调用 / ABSTAIN 1 / NOT_ALLOWED×2 各 1）、E2E initial_render OK；R1"≤16 调用"与 A3"ledger 2×案例数=16"一致 |
| 12 | run-records 无敏感信息 | ✓ 通读全文：无密钥、无 IP、无实例 ID；demo 答案文本未收录（自述与实测一致） |
| 13 | `_bmad-output/` git 状态 | `.gitignore:49` 忽略——PRD 本体不入仓库（背景事实，不构成 finding） |

---

## 四、三处措辞一致性专项核查（§3 / §7 / FR-14 / run-records）

| 表述维度 | §3 M-1 | FR-14 | §7 验收表 | run-records | 一致性 |
|---|---|---|---|---|---|
| 门定义 | 每题至少两次正确判断 | 每题至少两次正确判断；单轮不足以为证 | （未重述门定义） | R3 末"累计每案例 ≥2 次正确判断" | ✓ |
| 第一轮 | 8/8 | 8/8 | 8/8 | R1 逐案 8/8 | ✓ |
| 确认轮 | 7/7 有效判断 + 1 NETWORK（0 tokens，非判断错误） | 7/7 有效 + 1 NETWORK | 7/7 有效 + 1 NETWORK | R2 同 | ✓ |
| 补证 | 1/1 | 1/1 | 1/1 | R3 同（含 codes 与动作合法性） | ✓ |
| 结论 | 累计零判断错误 | （含于门） | 零判断错误 | 零判断错误 | ✓ |

上轮的核心病灶（三处措辞与真实记录不符）已完全消除，四处彼此咬合。唯一残留是 §3 反指标括注的"连续轮次"措辞（F-16，纯措辞级）。

另核：§7 路由行"NOT_ALLOWED 兼有真实记录与离线参数化覆盖"与 R4（2 条真实）+ 离线参数化测试并存，一致 ✓；§7 E2E 行与 R4/R5 的对应关系见 F-18(d)（技术性成立，建议显式化）。

---

## 五、验证为干净的项（本轮新增确认）

1. 上轮两条 high 的修复无"以新谎补旧谎"：O-6 描述的漂移现场复现为真；运行记录文件内容与 PRD 引用逐条吻合。
2. 修复未破坏 FR 编号体系（FR-1..FR-28 无重复；新增 FR-28 唯一）。
3. run-records 与既有台账无冲突（预算 16=2×8、NETWORK 0 tokens、codes 与动作形状与 FR-13 诊断口径一致）。
4. 红线 R-1..R-3 覆盖关系不受本轮修订影响（修订未触及 FR-1..FR-5、FR-18 等）。

---

## 六、建议的处置顺序

1. **推送 `cd0030c`**（一次 `git push` 即关闭 F-11）；推送后复核 `origin/main..HEAD` 为空。
2. **清理 presentation 文档中的公网 IP**（F-12），使 O-3/A6 的"不入库"声明成立；顺带确认仓库可见性策略。
3. 低级别项随下次文档修订一并处理：F-13 悬空引用自明化、F-14 §4 补编排线、F-15 节序、F-16 括注、F-17 A3 限定语、F-18 R2 逐案行（可选）。
