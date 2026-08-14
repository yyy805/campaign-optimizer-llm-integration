# Cynical Review（Blind Hunter / 对抗式）— 回溯式 PRD

- 被审对象：`prd.md`（大模型接入 Campaign Optimizer 解释员，as-built retrospective）+ `addendum.md`
- 审查日期：2026-08-06
- 审查方式：只读审查。所有可核实声明均对照仓库一手证据（`git log`、源码、fixtures、manifest、台账）逐项抽查，并对 release-pin 漂移做了实际复现运行。未修改被审文档。
- 审查立场：不信任文档自述，只信任仓库可复现证据；对"真实验收记录"类声明逐条追问"记录在哪里"。

---

## 总 Verdict

**PASS-WITH-FINDINGS**

文档整体质量显著高于一般回溯 PRD：抽查的约 26 处硬证据（commit 哈希、文件路径、测试名、数据集计数、公式、阈值、校验和）**全部属实**，未发现任何捏造。问题集中在两点回溯式文档的典型风险上：**(1) 验收证据强度被轻微夸大**（"8/8 ×2" 与真实轮次记录不符）；**(2) 一条已知的坏消息没有进入开放项**（main 工作区当前对自身的发布清单校验失败）。另有数条交付物未入档/记录不可追溯的中等问题。

| severity | 计数 |
|---|---|
| critical | 0 |
| high | 2 |
| medium | 3 |
| low | 5 |

---

## 一、证据抽查台账（26 处，全部通过）

| # | PRD/Addendum 声明 | 抽查结果 |
|---|---|---|
| 1 | commit `38fcc6d`（契约/收敛门禁证据） | ✓ 存在：`merge(llm): integrate canonical R5 convergence with v13 pilot` |
| 2 | commit `4fee4e3`（FR-13 行级安全诊断） | ✓ 存在：`feat(llm): record safe decision diagnostics in v14 eval rows` |
| 3 | commit `5c4e6d5`（FR-25 Dockerfile） | ✓ 存在：`feat(ops): add Dockerfile for headless Streamlit demo` |
| 4 | commit `2724f02`（FR-27 交接/推送） | ✓ 存在：`docs: add ontology team handoff for cloud deployment` |
| 5 | FR-2 六字段发布身份 + 参数化测试 | ✓ `release_pin.py` `IDENTITY_FIELDS` 恰为 6 字段；`test_each_release_identity_mutation_fails_before_provider_construction` 按 6 字段参数化，含 sentinel 不回显断言 |
| 6 | FR-3 `release_pin.py` + `test_ontology_release_pin.py` | ✓ 均存在；`PackageDriftError` 语义与声明一致 |
| 7 | FR-4 测试 `test_pending_r5_cannot_be_retrieved_as_decisive_llm_evidence` | ✓ 存在于 `tests/test_llm_convergence_integration.py`，断言 `INACTIVE_RULE` |
| 8 | FR-5 测试 `test_candidate_contradicting_committed_review_verdict_fails_closed_without_echo` | ✓ 存在，含"不回了伪造值"断言 |
| 9 | FR-9 `BudgetLedgerV12`（每候选每角色≤2、triage≤1、总量封顶） | ✓ `agent_workflow_v12.py` 逐条核实 |
| 10 | FR-6/FR-7/FR-8 引用的 `output_guard.py`/`exchange.py`/`reviewer_binding_v13.py`/`intent_policy.py` | ✓ 均存在 |
| 11 | FR-10 提示词哈希钉死 + 加载器校验 | ✓ `load_role_configuration` 对 prompt hash 与 tool schema hash 均做 mismatch 即 `ValueError` |
| 12 | FR-11 冻结考卷 8 案例 | ✓ `reviewer_judgment_v1/cases.json` 恰 8 case（3 pass / 4 revise / 1 reject-injection） |
| 13 | FR-12 canonical R5@2.0-campaign-pending | ✓ `kb_export/v1/manifest.json` `release_identity.rule_version` 完全一致 |
| 14 | FR-17 50 题路由安全数据集 | ✓ `routing-safety.json` 恰 50 case（15 类行为分布） |
| 15 | FR-18/19 导出忠实投影、状态显式 | ✓ 7 张规则卡 + manifest：R5=PENDING_HUMAN_REVIEW、R7=RETIRED、其余 ACTIVE |
| 16 | FR-20/21 百炼库 `eeirxr7djz` @ 阈值 0.60、台账 | ✓ `docs/knowledge-base-publications.md` 有台账行 + S3 五硬门逐项结果（含具体召回百分比，并注明默认 0.20 会让污染/探针门失败） |
| 17 | FR-22/23/24 app.py 侧边栏/dry-run 默认/安全失败类别 | ✓ `app.py` 逐项核实（侧边栏只显示凭据存在性、`value=True` 的 dry-run 勾选框、异常分支只给安全类别） |
| 18 | FR-23 证据"AppTest" | ✓ `tests/test_streamlit_ui.py` 使用 `streamlit.testing.v1.AppTest` |
| 19 | FR-25 Dockerfile 含 bundles + headless | ✓ `COPY .ontology_bundles`、`COPY tests/fixtures`、`--server.headless true` |
| 20 | FR-26 交接文档三要素 | ✓ `docs/handoff-ontology-team-2026-08-06.md` 含架构不变式、部署清单、R5 转正重发布流程 |
| 21 | §7"契约/收敛门禁 14 项" | ✓ `test_llm_convergence_integration.py` 收集恰 14 个 test case（1+6+1+1+1+4） |
| 22 | M-4"587 项测试" | ✓ pytest collect-only 得 587 + `test_streamlit_ui.py` 1 项 = 588，与"587 passed / 1 skipped"吻合（本审查 venv 缺 streamlit 导致该模块 import 错，属审查环境问题） |
| 23 | Addendum A1 runner 谱系、ReviewerPacket digest 公式 | digest ✓ 与 `agent_workflow_v5.py` 一致（candidate_id+task+trusted+candidate+reviewer_prompt_hash）；谱系见 F-06 |
| 24 | Addendum A3 预算公式 `4*(rounds+1)+triage` | ✓ 与 `max_provider_calls_v12` 逐字一致（baseline=4、chat=5） |
| 25 | Addendum A4 模型别名与采样参数 | ✓ `agent_roles.v15.json`：triage=qwen3.6-flash / executor=qwen3.7-max / reviewer=qwen3.7-plus；`temperature=0, stream=False, enable_thinking=False`（v13） |
| 26 | Addendum A5/A6 bundle 与 checksum、环境变量 | ✓ `.ontology_bundles/a83ff2b4…`（checksum `626cfbde…` 与 manifest/台账一致）与历史包 `b90391ed…` 均存在；`.env.example` 含 DASHSCOPE_API_KEY / DASHSCOPE_WORKSPACE_ID / LLM_TIMEOUT_SECONDS；Amendments 节存在于数据集 README（00cb04b 对应） |

**结论：未发现任何虚构的 commit、文件或测试。证据真实性这条线是干净的。**

---

## 二、Findings

### F-01 [severity: high] 位置：§3 M-1、§7 验收表"Reviewer 考卷"行、FR-14
**问题**：验收证据强度被夸大——"8/8 ×2"与真实轮次记录不符。
**证据**：
- PRD 把验收门定义为"**连续两轮全对**才算验收"，并在 M-1 与 §7 中两次写"实际：8/8 ×2 + 补考"。
- 但真实记录是：第一轮 8/8；**确认轮 7/7 有效判断 + 1 次 NETWORK 失败**（代理抖动，0 tokens）；随后补证 `--case pending_revise_definitive_verdict` 1/1。
- 同仓库的交接文档（`docs/handoff-ontology-team-2026-08-06.md` §1）措辞是"两轮+补证零判断错误"，**没有**说"8/8 ×2"——两份文档对同一事件的描述不一致，且 PRD 版本是更强的那个。
**定性**：这正是回溯式文档的典型病灶——把"累计每案例 ≥2 次正确判断"包装成"连续两轮全对"。实质结论（零判断错误）成立，但验收门的达成方式被美化了。
**建议**：M-1 与 §7 改写为如实版本："第一轮 8/8；确认轮 7/7 有效判断 + 1 次 NETWORK（0 tokens，非判断错误）；补证 1/1。累计每案例 ≥2 次正确判断，零判断错误。"并说明这满足的是"逐案例双次正确"而非字面"连续两轮全对"，或修订 M-1 的门定义。

### F-02 [severity: high] 位置：§9 开放项（缺失项）、FR-3/FR-12、FR-27
**问题**：已知坏消息未入开放项——**main 工作区当前对自身的发布清单校验失败（PackageDriftError）**，PRD 却把 release-pin 呈现为全绿交付。
**证据（本审查现场复现）**：
- `load_publication_manifest()` 加载成功（checksum `626cfbdedc95…`），但 `verify_publication_manifest(m)` 以仓库根为 root 运行时抛 `PackageDriftError: ontology publication manifest does not match package files`（exit 1）。
- 对照之下，LLM 运行时路径 `release_pin.load_verified_manifests()`（以 `.ontology_bundles/<source_commit>/` 为 root）校验通过——即**运行时安全，但工作区树与钉住发布存在真实漂移**（根因：canonical/Hannah 集成分支未合并，合并顺序导致工作区文件与发布包不一致）。
- 未入库的 `docs/progress-report-2026-08-06.md` 如实写了"核查发现工作区与发布清单存在漂移……合并与回归方案已定"，但 **PRD §9 只字未提**；O-4 只说分支未合并，未说其后果就是工作区过不了自己定义的门。
**定性**：fail-closed 门禁机制本身工作正常（它确实拦住了），PRD 把这当成纯胜利叙事，却省略"门此刻正在对我们自己的工作区报警"这一事实。对以诚实为卖点的回溯 PRD，这是实质性遗漏。
**建议**：§9 增加开放项（如 O-6）："main 工作区对 `publication_manifest.json`（626cfbde…）的根级校验当前抛 PackageDriftError（合并顺序漂移）；运行时经 bundle 级校验安全。待 canonical/Hannah 合并 + 文件对齐后重跑至 MANIFEST_OK。"FR-27 的"工作区干净"同步加注（见 F-07）。

### F-03 [severity: medium] 位置：FR-8/FR-14/FR-15/FR-16 证据栏、§7 验收表
**问题**：多项"真实记录"类证据在仓库中不存在，验收不可追溯复验。
**证据**：全仓库 grep 确认：v9 两轮评测运行记录、E2E 真实运行记录、路由安全真实运行记录（REFUSED/ABSTAIN/NOT_ALLOWED）均无任何入库工件；仓库内仅有的真实运行痕迹是数据集 README 的 Amendments 注记（"two measured v9 runs used …"）与知识库台账的召回百分比。其中 **FR-16/§7 声称的"真实 TRIAGE_INTENT_NOT_ALLOWED 记录"尤其存疑**：可查证的真实 chat 运行只产生了硬路由 REFUSED（0 调用）与 TRIAGE_ABSTAIN 两类结果；NOT_ALLOWED 路径目前只有离线参数化测试覆盖。
**定性**：PRD 开篇承诺"证据为 commit 哈希/测试数/验收记录"，但 M-1、M-3、E2E 三个门的证据实际存在于仓库之外的会话记录里，读者无法复核。
**建议**：要么把脱敏后的运行记录（评测行/运行 JSON）提交入库（如 `tests/fixtures/llm_eval/runs/`），要么把 §7 相应行的证据栏降级为"仓库外会话记录，未入库"，并明确 NOT_ALLOWED 目前仅有离线覆盖。

### F-04 [severity: medium] 位置：§5 FR 清单（完整性缺失）
**问题**：已交付能力未写入任何 FR——`LocalLLMOrchestrator` + `SessionStore`。
**证据**：`campaign_optimizer/llm/orchestrator.py`（commits `80fe8f6`/`eeca02a`：服务端 chat 门禁、`OrchestrationResult`/`AttemptMetadata` 审计、fail-closed 路由）与 `session_store.py`（租户/用户级会话隔离边界）已交付：被 `llm/__init__.py` 公开导出、被 `eval_runner.py` 使用、有专门测试（`test_local_llm_orchestrator.py`）、有配套架构文档（`docs/architecture/llm-integration-local-first.md`）。PRD 的范围与 FR 清单（三角色管道视角）完全没有提及这条并行交付线。
**建议**：§4 范围或 §5 增加一条 FR/说明，定位为"已交付的 local-first 编排层（含会话隔离与审计元数据），demo 主路径使用 three-role runner"；如属有意弃用，也应写明取舍理由——回溯文档不应让已交付代码隐形。

### F-05 [severity: medium] 位置：§9 开放项（缺失项）、FR-25
**问题**：Docker 镜像配方"从未在任何环境构建成功过"这一事实未列入已知限制。
**证据**：FR-25 声称 Dockerfile 交付（属实，文件与内容已核实）。但本地 Docker 引擎因 BIOS 未开 VT-x 无法启动（交接对象文档 `docs/progress-report-2026-08-06.md` 明确写了"本地 Docker 起不来……镜像直接在 ECS 构建"），ECS 部署也尚未执行（O-3）。因此**镜像配方至今未经过一次真实构建验证**——`uv sync --frozen` 可达性、bundles 复制、headless 启动都是纸面推断。
**定性**：FR-25 没有夸大（它只声称文件内容），但 §9 把"镜像未验证"这条对手接手至关重要的限制漏掉了。
**建议**：§9 增加开放项："Dockerfile 未经过真实构建验证（本地引擎不可用，待 ECS 首构）；首次构建风险见交接文档部署清单。"

### F-06 [severity: low] 位置：Addendum A1
**问题**：runner 谱系"three_role_runner_v5–v13"不准确。
**证据**：仓库中存在 `three_role_runner_v6.py`…`v13.py` 与无版本号基座 `three_role_runner.py`，**不存在 v5 runner**（只有 `agent_workflow_v5.py`）。
**建议**：改为"three_role_runner v6–v13 + 基座 three_role_runner.py（谱系自 v5 工作流契约演化）"。

### F-07 [severity: low] 位置：FR-27
**问题**："工作区干净"需要限定语。
**证据**：tracked 树确实干净（无已修改文件），`origin/main == HEAD`（全部已推送，`git log origin/main..HEAD` 为空）✓；但当前存在 8+ 个未入库路径（`.continue/`、`.worktrees/`、`docs/progress-report-2026-08-06.md`、`review-integrity.md`、`review-reality.md`、本 PRD 目录自身等），且工作区对发布清单存在漂移（F-02）。
**建议**：改为"tracked 树干净、全部提交已推送"，并注明验收/进度类记录仍为未入库工件（与 F-03 联动）。

### F-08 [severity: low] 位置：NFR-2
**问题**：成本数字（约 45 次调用 / 约 15 万 tokens）是自报数，非可复现证据。
**证据**：仓库内唯一落点是 `docs/presentation-llm-integration-story.md` 的一句问答；无用量台账/日志入库。
**建议**：保留"约"字即可，但建议补一句"自报口径，无逐次台账"，或在未来真实调用时留存用量回执。

### F-09 [severity: low] 位置：§9 O-3、Addendum A6
**问题**：ECS 细节（实例 ID `i-uf6ctiuazm4gv5cvzyje`、公网 IP `47.102.115.250`、到期日）无法在仓库内验证，且属基础设施标识符入库。
**证据**：外部云资源事实，仓库无法佐证；NFR-5 承诺"不含密钥、客户数据"——目前确实无密钥，但内部 IP/实例 ID 已随文档推送至 GitHub。
**建议**：确认仓库可见性策略（私有则无碍）；如仓库可能公开，把实例标识移到内部台账，PRD 只留"上云由本体团队执行"。

### F-10 [severity: low] 位置：§3 M-4 vs §7 验收表
**问题**：测试总数口径不一致（表述层面）。
**证据**：M-4 写"587 项测试全绿"；§7 写"587 passed / 1 skipped"。实收集 588 项（587 + 1 skipped），"587 项"严格说漏掉了被 skip 的那项。
**建议**：M-4 改为"588 项收集：587 passed / 1 skipped，零真实调用"。

---

## 三、验证为干净的项（不重复展开）

1. **红线覆盖**：R-1（只解释不裁决）由 FR-4/FR-5/FR-11/FR-18 + §4 确定性投影覆盖；R-2（未批准必须明说）由 FR-4/FR-11/FR-19/FR-20 覆盖；R-3（过不了门禁就安全回退）由 FR-2/3/5/6/8/15/16 + NFR-1 覆盖。**R-1..R-3 均被 FR 完整覆盖。**
2. **FR 编号稳定性**：FR-1..FR-27 连续、无重复、无跳号；PRD↔Addendum 无互相矛盾的技术参数（bundle/checksum/阈值/别名/预算公式/数据集计数全部交叉一致）。
3. **内部一致性**：除 F-01（8/8×2 措辞）与 F-10（口径）外，PRD 与 addendum、交接文档、台账之间无冲突。
4. **反指标设计**（过度修订率用连续轮次度量）与冻结考卷+硬门机制在代码中真实存在且与描述一致。
5. **已知限制的诚实度总体良好**：Triage flash 误路由（O-2）、R5 待转正（O-1）、Executor 首轮类型错（O-5）、分支未合并（O-4）均如实记录——遗漏的是 F-02/F-05 两条。

## 四、无法在仓库内核实的声明（标注，非 finding）

- ECS 实例/到期日/跨区连通性（外部事实，见 F-09）。
- 所有"真实运行"验收的原始会话记录（见 F-03）。
- 百炼侧"截图验收"的截图本体（台账中的百分比数字已入库，截图未入库）。

---

## 五、建议的处置顺序

1. 先修两处 high：§3/§7/FR-14 的"8/8 ×2"改为如实轮次记录；§9 补工作区漂移开放项。
2. 再补三处 medium：真实运行记录入库或降级标注；补 orchestrator/session_store 交付说明；补"镜像未构建验证"开放项。
3. low 项随下一次文档修订一并处理。
