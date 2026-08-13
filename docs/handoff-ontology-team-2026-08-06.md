# 交接说明：大模型接入侧 → 本体团队（2026-08-06）

本体团队负责上云（ACR/ECS）。本文给出架构不变式、交付物与整合清单，目标零沟通成本接手。

## 1. 当前状态

`main`（推送至 `891faf2`）包含完整可部署 demo：三角色 Agent（Executor/Reviewer/Triage，Function Calling v13）、Reviewer v9 提示词（冻结测评集验收关闭：两轮+补证零判断错误）、Streamlit demo（`app.py`）、ECS 直跑部署配方（**不用 Docker**，2026-08-10 与老师确认；`Dockerfile` 保留为备选）、轻量 RAG 导出与百炼知识库发布（ID `eeirxr7djz`，检索验收通过）。

## 2. 架构不变式（修改任何部分前必读）

1. **混合 RAG**：权威规则上下文走 release-pin 确定性投影（`campaign_optimizer/llm/release_pin.py`，按 review 钉死的 ID 投影，校验和验证，fail-closed）。百炼知识库**只是补充检索通道**，检索结果是不可信数据，永不参与裁决。
2. **检索不得改变**规则定义、阈值、适用范围或限制；"查 R5 只返回 R5 或明确无结果"是硬门。
3. **相似度阈值 0.60 是发布配置的一部分**（默认 0.20 会让污染与探针硬门失败）。任何重新发布必须带此阈值并重跑 `tests/fixtures/kb_retrieval_v1/questions.json`。
4. **版本对应必须记录**：每次知识库发布在 `docs/knowledge-base-publications.md` 追加一行（KB ID ↔ `kb_export/v1/manifest.json` 的 package_checksum）。
5. 提示词与角色配置哈希钉死（`agent_roles.v15.json`）；改 Reviewer 语义必须升版（v10+）并用冻结测评集复验，不原地编辑。

## 3. 交付物清单

| 路径 | 用途 |
|---|---|
| `Dockerfile` / `.dockerignore` | 备选交付物（未经验证；正式方案为 ECS 直跑，见 §4） |
| `app.py` | Streamlit demo（dry-run 默认；真实调用需环境变量） |
| `kb_export/v1/` | 知识库导出快照（7 文档 + manifest）；`scripts/export_knowledge_base_v1.py` 重新生成 |
| `docs/knowledge-base-publications.md` | 发布版本台账 |
| `tests/fixtures/kb_retrieval_v1/questions.json` | 冻结检索验收题（12 条，关键 5 条已真实验收） |
| `scripts/run_reviewer_judgment_eval_v14.py` / `run_three_role_e2e_v15.py` | 真实评测入口（默认 dry，`--real` 才调用） |

## 4. 部署清单（ECS 直跑，不用 Docker）

1. SSH 上 ECS（Ubuntu 22.04）；安装 Python 3.14 + uv
2. 仓库获取：ECS 直接 `git clone`（或本地 `git archive` 后 scp）
3. 依赖：`uv sync --frozen`（中国网络走阿里云索引：UV_INDEX_URL / pip -i mirrors.aliyun）
4. 环境变量经 systemd EnvironmentFile 外置：`DASHSCOPE_API_KEY`、`DASHSCOPE_WORKSPACE_ID`（北京区域），可选 `LLM_TIMEOUT_SECONDS`；不入库
5. systemd 服务跑 `python -m streamlit run app.py --server.headless true --server.port 8501`，开机自启/崩溃自拉
6. nginx 反代（可选）；安全组开 8501
7. 冒烟：UI 侧边栏应显示 `R5@2.0-campaign-pending` 与 `reviewer_v9`；dry-run 零调用；release-pin 自校验通过（仓库自带 `.ontology_bundles` 与 `tests/fixtures`）

## 5. 需本体团队确认/协作

1. **联合验收**：确认 `docs/knowledge-base-publications.md` 中 `eeirxr7djz` 行与当前发布包对应关系。
2. **R5 转正流程**：R5 人工审核通过后，规则卡状态变更 → 重新发布本体包 → 我方 `export_knowledge_base_v1.py` 重新导出 → 重新上传 → 阈值 0.60 → 重跑检索验收 → 台账追加新行。
3. **已知限制**：qwen3.6-flash Triage 对评审类问句系统性误路由（已 fail-closed，UI 主路径用 initial_render 兜底）；Executor 首轮偶发 `limitations_included` 类型错，内置修复循环消化（设计内）。
