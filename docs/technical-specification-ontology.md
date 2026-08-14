# 技术规范（终稿）· 本体侧审查系统

**版本**：v1.0（2026-08-14）　**状态**：回溯式正式化完成，待 review⑧
**输入**：prd.md / ARCHITECTURE-SPINE.md / epics / 测试设计三件 / 追溯矩阵 / 实现记录 / sprint-status / 换库演练 runbook 与实测 / 八轮 review 决断记录
**范围**：仅本体侧（规则库 / 审查引擎 / 发布钉扎 / Review API / 部署）；LLM 解释层、RAG、Demo 见 LLM 侧文档

---

## 1. 概述

- **目的**：为广告优化建议提供形式化、版本化、可审计的业务规则审查。核心原则：**规则负责判断，大模型负责解释，一切按版本可追溯**。
- **读者**：课程评审、本体团队、LLM 团队（接口方）、运维。
- **术语**：本体=机器可读规则资产集合；manifest=发布身份清单（六字段+逐文件哈希）；bundle=冻结发布快照；fail-closed=校验失败即拒绝；台账=版本与治理记录账本；置信度修订=带校验的运行时置信度调整（须与规则卡版本及阈值一致，否则拒绝）。

## 2. 技术栈

| 组件 | 选型 | 理由 |
|---|---|---|
| 语言 | Python 3.14（.python-version） | 项目基线 |
| 依赖管理 | uv + uv.lock（--frozen） | 构建可复现；直跑部署的依赖一致性靠它补偿 |
| 服务框架 | FastAPI 0.116.1（API 分支钉死） | 异步、schema 原生 |
| 运行库 | SQLite（demo 基线）/ 阿里云 PG（演练通过，部署切换） | boring technology + 老师提议 |
| 迁移 | Alembic（正向只进） | 降级在 DDL 前失败，护审计链 |
| 部署 | ECS 直跑 + systemd（无 Docker，2026-08-10 决策） | 本地 VT-x 受限；团队 Dockerfile 保留为备选交付物 |

## 3. 架构设计

**范式：确定性核心、不受信边缘**（release-pinned 规则引擎）。

| 层 | 目录/模块 | 信任级别 |
|---|---|---|
| 规则资产层 | campaign_optimizer/ontology/{concepts,rules,guardrails,schemas,clients,assertions} | 权威（经发布流程） |
| 确定性引擎层 | condition_evaluator / review_engine / review_workflow / contracts 聚合 | 权威 |
| 发布信任层 | publication.py / manifest / .ontology_bundles/ / 使用方 release_pin | 信任锚 |
| 运行时状态层 | db.py + Alembic | 内部可信 |
| 服务层 | ontology review api（FastAPI，集成分支） | 内部可信 |
| 不受信边缘 | LLM 侧（本规范之外），仅经 release_pin 投影接入 | 不可信 |

**"冲突"三层语义**：① 方案动作 vs 规则策略（review_policy 映射→SUPPORT/CONFLICT/otherwise 含 NOT_APPLICABLE）；② 多规则分歧→独立出条+保守聚合取最坏（SUPPORT < NOT_APPLICABLE < UNVERIFIED < INSUFFICIENT_EVIDENCE < CONFLICT），禁止 LLM 仲裁；③ 证据自相矛盾→契约错误拒绝。

**架构决策（AD-1..17 要旨）**：裁决归确定性代码；发布身份六字段+逐文件哈希；冻结 bundle 为唯一信任锚；fail-closed 零豁免；状态所有权二分（运行时进库、规则进文件）；治理闭环（反馈≠修改，审批人=本体团队负责人，台账留痕）；判定语义与保守聚合；实现规则集显式版本化（v1={R5}）；历史不可变；风险两维分离（置信度 vs 可逆性）；迁移只前进；DB 兼容边界；直跑部署包络；不认证姿态+触发条件成文；契约演进随发布身份；版本递增不原地编辑；可观测即审计面。

## 4. 配置管理

- **发布配置**：manifest 六字段（ontology/rule/engine/schema 版本 + source_commit + package_checksum）+ 逐文件 sha256/size；阈值类配置（如检索阈值 0.60）随发布走（LLM 侧携带）。
- **运行配置**：env 外置；API 侧 `DATABASE_URL`/`ONTOLOGY_DATABASE_URL` **必须带 connect_timeout**（代码对缺省空串转数字会崩，演练实测）；仓库与部署目录不含密钥（.env.* 被 gitignore 覆盖）。
- **版本化约定**：角色/规则/引擎配置以版本号文件钉死（v5→v15 化石保留），改语义必须升版复验。

## 5. 部署方案

- **形态**：ECS 上海单实例（2 核 4G，Ubuntu 22.04，到期 2026-09-12；约 09-05 定续费/迁移）直跑：uv sync --frozen → python/uvicorn 起服务 → **systemd 保活**（开机自启/崩溃自拉）→ 安全组开 8000（8501 属大模型侧 demo）。
- **数据库**：阿里云 RDS PG 18（实例 pgm-uf6hl30c1vr9v8zr3o；库 mta_data 真库 + mta_data_test 体检库）。**换库演练 1–5 步全绿**（2026-08-14）：体检 1 passed、建表 13 张、空搬对账 OK、**双跑 PASS（16 项跨库一致）**；切换+台账（runbook 6–7 步）随部署执行；SQLite 文件切换后保留两周兜底。
- **冒烟三件套**：/ready 200 + 创建 review 201 + 同幂等键重放返回同一 review_id。
- **备选**：团队 Dockerfile/compose 保留，本期不采用。

## 6. 资产与快照处理

- 规则资产=文件（JSON+Schema 校验），git 版本化；发布前盘点（54/54 基线，含 assertions 迁移验收资产）。
- 发布=manifest+冻结 bundle（`.ontology_bundles/<source_commit>/`）随码分发；使用方逐条验真，任一不符抛 PackageDriftError 拒跑。
- 历史 manifest 与快照同目录保留、不可变；旧审查只以当代快照解析。
- 知识库导出（kb_export）属 LLM 侧；本侧只提供身份与快照。

## 7. 安全设计

- **姿态**：内部 demo 不认证+网络层隔离；触发认证条件成文（多用户/公网暴露/治理跨团队边界）。
- **fail-closed 清单**：漂移拒跑、跨版本拒解析、降级拒执行、未实现规则拒启用、启动自校验不过不服务。
- **密钥**：不入仓库/不入部署目录（T-20 含扫描）；env 注入。
- **治理边界**：反馈→治理任务→人工审批→重发布；无反馈→规则自动写路径。
- **运营注意**：演练期间密码曾明文出现在终端/截图，演练后须轮换（已提醒用户）。

## 8. 开发规范

- 版本递增、不原地编辑；证据三态标签（recorded / pending-merge / pending-exec）。
- **流程治理**：每步产出→对抗审查→用户逐项决断（本次共八轮，决断全记录于各 memlog 与文档注记）。
- 迁移正向只进；**方言分菜**（SQLite 走 batch 拆表特招、PG 走逐条加列加约束，2026-08-14 演练发现两库口味相反）。
- 测试基线即行为契约：主线 ≥586 且 canonical 套件绿（合并门后）。

## 9. 监控与日志

- 关键事件结构化日志：发布校验/判定生成/反馈入库/治理状态迁移；留存随运行库。
- 探活：/health、/ready；启动 release-pin 自校验结果可见。
- 审计面：版本台账（发布身份+治理记录+换库事件一行）。

## 10. 性能需求

- 目标：单实例 2C4G、课程/demo 量级；SQLite 写串行化（BEGIN IMMEDIATE）足够。
- 非目标：多实例/高并发（触发条件成文：多实例需求出现→先换 PG 再谈横向）。
- 豁免：量化 SLO 豁免（2026-08-12 用户决断，理由单实例 demo）。

## 11. 兼容性

- Python 3.14；API 侧约束 >=3.12,<3.15 兼容。
- 数据库双方言：SQLite 基线 + PG（演练通过）；迁移按方言分菜。
- 跨区：ECS 上海调北京百炼（LLM 侧）已确认可行。
- 开发机注记：本地 Docker 需 BIOS 开 VT-x（本次未装成，直跑方案绕过）。

## 12. 常见问题与排查（全部来自本次实测）

| 现象 | 含义 | 处置 |
|---|---|---|
| PackageDriftError | 工作区与 manifest 不符 | 使用方改走 bundle 根；工作区漂移走合并门 |
| downgrade 报错 | 设计如此（迁移只前进） | 不处置；数据完好即正确 |
| whitelist/pg_hba/refused | 白名单未加 | 控制台加公网 IP |
| password authentication failed | 账密错 | 核对控制台 |
| 连接串解析错（host 含 2024@…） | 密码含 @ 未转义 | @→%40 等百分号转义 |
| alembic 报 invalid interpolation | 老解析器把 % 当占位符 | 密码改走 PGPASSWORD，连接串不含密码 |
| connect_timeout 崩（ValueError） | 连接串缺 connect_timeout | 补 &connect_timeout=10 |
| SQLite 报 No support for ALTER of constraints | SQLite 须 batch 特招 | 迁移按方言分菜 |
| PG 报 DependentObjectsStillExist | 被外键引用的约束不许拆 | 同上（PG 走逐条） |
| alembic_version 主键冲突 | 台账本不该搬 | 搬迁脚本已排除 |
| 操作侧：PowerShell 用 `set` 记钥匙不生效 | `set` 是 cmd 方言，PowerShell 不认 | 用 `$env:VAR="..."` |
| 操作侧：`--yesuv` 未识别参数 | 两行命令粘贴时连成一行 | 逐行独立粘贴，粘完检查换行 |

---

## 附录 A · 决策日志（要旨）

- 08-06：ECS 搁置先汇报；08-10：无 Docker 直跑+systemd；换库"验证过地转"五步；R5 审批人=本体团队负责人（不加老师会签）；08-12：review④豁免性能 SLO；08-13：PG 凭据到手、建 mta_data_test；08-14：迁移方言分菜+台账表排除；演练 1–5 全绿。

## 附录 B · 交付物清单

- 需求：`_bmad-output/planning-artifacts/prds/prd-ontology-review-2026-08-06/{prd,addendum}.md`
- 架构：`.../architecture/architecture-ontology-review-2026-08-07/{ARCHITECTURE-SPINE,technical-specification-draft,架构白话解读}.md`
- 故事：`.../epics-ontology-review-2026-08-07.md`；sprint：`.../implementation-artifacts/sprint-status.yaml`
- 测试：`.../test-artifacts/{test-design-architecture,test-design-qa,traceability-matrix}.md`、`test-design/ontology-review-handoff.md`
- 实现记录：`.../implementation-artifacts/implementation-records.md`
- 演练：`docs/pg-drill-runbook.md`、`scripts/drill_*.py`
- 就绪报告：`.../implementation-readiness-report-2026-08-10.md`
- 老师汇报类：`docs/progress-report-2026-08-06.md`、`docs/architecture-diagram-2026-08-06.html`

## 附录 C · 追溯与状态汇总

- FR 31/31 全映射（T-01..T-29）；CONDITIONAL PASS：recorded 绿，pending-merge 随合并门（Epic 1），pending-exec 随部署/演练窗口。
- 实现状态：recorded 11 / pending-merge 10 / pending-exec 4（口径：Epic 2–6 在审 25 故事；Epic 1/7 的 7 个 backlog 故事不计入此三态）。
- 剩余工作：合并门（Epic 1，含迁移 PG 移植性修正并入）、部署切换+台账（runbook 6–7）、R5 转正演练、密码轮换、ECS 续费决策（09-05）。
