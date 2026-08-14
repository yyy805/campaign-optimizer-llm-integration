---
status: final
created: 2026-08-14
---

# 大模型接入（Campaign Optimizer 解释员）技术规范

## 1. 系统概述

大模型接入系统为广告审核流程提供"AI 解释员"：把投放方案与本体审核结论翻译成人话。红线：模型**只解释、不裁决**；未批准内容必须说"还没批准"。系统采用"概率组件套在确定性门禁里"的架构：三角色（门卫/写手/质检员）产出建议，代码门禁持有全部裁决权，任一门禁失败即 fail-closed 安全回退。知识走双通道：权威内容按编号直取（release-pin 确定性投影），百炼知识库仅作补充检索（不可信数据，永不参与裁决）。

## 2. 技术栈

### 2.1 后端技术栈

| 组件 | 版本 | 说明 |
|------|------|------|
| Python | 3.14 | 主要开发语言 |
| uv | frozen lock | 依赖管理，uv.lock 冻锁保证一致性 |
| jsonschema | 契约主体 Draft-07；决策信封 Draft 2020-12 | 契约模板校验 |
| Streamlit | 1.61.x（lock 1.61.1） | 演示网页 |
| pytest | 9.x | 离线守护测试（571 通过/1 跳过） |

### 2.2 模型与检索层

| 组件 | 型号/配置 | 说明 |
|------|------|------|
| 门卫（triage） | qwen3.6-flash | 路由分类，失败/低置信即安全回退 |
| 写手（executor） | qwen3.7-max | 起草解释，过输出守卫 |
| 质检员（reviewer） | qwen3.7-plus | Function Calling 唯一通道提交决策 |
| 端点 | OpenAI 兼容，cn-beijing | 环境变量注入 |
| 知识库 | text-embedding-v4 + qwen3-rerank | 阈值 0.60（机器化进导出清单） |

### 2.3 部署环境

| 组件 | 版本 | 说明 |
|------|------|------|
| 操作系统 | Ubuntu 22.04 LTS | ECS 上海区域，跨区调北京百炼 |
| systemd | 系统自带 | 服务保活 |
| Nginx | 最新稳定版 | 反代 8501 |
| Docker | **不用**（老师确认） | Dockerfile 保留为备选交付物 |

## 3. 系统架构

### 3.1 整体架构

```
┌──────────────┐   ┌───────────────────────────────┐   ┌──────────────┐
│ 网页(边缘层)  │──►│ 三角色管道 + 确定性门禁(后端)     │──►│ Qwen(仅推理)  │
└──────────────┘   └───────────────┬───────────────┘   └──────────────┘
                                   │
                 ┌─────────────────┼─────────────────┐
                 ▼                   ▼                   ▼
          权威源(release-pin     知识库(不可信数据,     契约模板(五份
          按编号直取)            仅补充检索)            JSON Schema)
```

### 3.2 数据流

1. 用户提问 → 硬路由（复合/恶意零调用拒答）→ 无锚定才交门卫分类
2. 写手起草 → 输出守卫（限制完整披露、数值接地）
3. 质检员经 Function Calling 提交决策 → 本地 binding 校验（摘要/白名单/动作语义）
4. 门禁全过才出回答；任一失败 → 固定文案安全回退，不回显不可信值

### 3.3 架构不变式（摘要，全文见架构脊）

| 编号 | 不变式 |
|---|---|
| AD-1 | 混合 RAG 权威分割：检索永不参与裁决 |
| AD-2 | fail-closed 信封：失败即安全回退、不回显 |
| AD-3 | 发布身份钉死：校验根=冻结 bundle；多匹配即熔断；阈值机器化 |
| AD-4 | 不可变工件与版本盖章 |
| AD-5 | Function-Calling 唯一质检通道 |
| AD-6 | 自动放行仅限"可逆且仅解释"输出（置信度×可逆性双维度） |
| AD-7 | 预算封顶 |
| AD-8 | 测量纪律：冻结考卷+事先声明验收 |
| AD-9 | 硬路由先于门卫 |
| AD-10 | 部署包络：ECS 直跑 |
| AD-11 | 本地优先编排线为显式豁免项 |

## 4. 配置管理

### 4.1 环境变量

- `DASHSCOPE_API_KEY`：模型密钥（北京区域）
- `DASHSCOPE_WORKSPACE_ID`：工作空间 ID
- `LLM_TIMEOUT_SECONDS`：可选超时
- 注入方式：systemd EnvironmentFile；**不入库**；网页只显示凭据存在性，永不显示值

### 4.2 版本盖章配置

- `agent_roles.vN.json`：提示词/工具 schema 哈希钉死；加载校验 mismatch 即报错
- 质检员提示词谱系 v6→v9、runner 谱系 v6–v13 逐版继承，不原地编辑
- 考卷/路由集/检索集/导出：冻结；修订走带文档的 amendment 流程

### 4.3 发布钉死

- 六字段身份（ontology_version/rule_version/engine_version/schema_version/source_commit/package_checksum）+ 条目级 sha256/size 双验
- 运行时校验根 = 冻结 bundle 目录；工作区根校验 = 合并健康检查（O-6，待复验）
- 路径防穿越（绝对路径/`..`/重复拒）；畸形清单抛漂移错误

## 5. 部署规范

### 5.1 系统依赖

Ubuntu 22.04：Python 3.14、uv、git、nginx、curl。

### 5.2 部署步骤（ECS 直跑，不用 Docker）

1. SSH 上 ECS；安装 Python 3.14 + uv
2. 仓库获取：ECS 直接 git clone（或本地 git archive 后 scp）
3. `uv sync --frozen`（中国网络走阿里云索引：UV_INDEX_URL / pip -i mirrors.aliyun）
4. env 文件经 systemd EnvironmentFile 注入
5. systemd 起服务；nginx 反代；安全组开 8501
6. 冒烟（见 5.5）

### 5.3 Nginx 配置

反代至 127.0.0.1:8501；`proxy_read_timeout 600s`；按需启用 HTTPS。

### 5.4 Systemd 服务配置

```ini
[Unit]
Description=Campaign Optimizer Explainer (Streamlit)
After=network.target

[Service]
Type=simple
WorkingDirectory=<repo>
ExecStart=<venv>/bin/python -m streamlit run app.py --server.headless true --server.port 8501
Restart=always
RestartSec=5
EnvironmentFile=<repo>/.env

[Install]
WantedBy=multi-user.target
```

### 5.5 冒烟清单

侧边栏显示 `R5@2.0-campaign-pending` 与 `reviewer_v9`；dry-run 零调用；release-pin 自校验通过（bundle 根）。

## 6. 知识与数据处理规范

### 6.1 权威源

- 本体发布包为唯一权威；按 review 钉死编号直取原文；status≠ACTIVE 不得作为决定性证据
- 候选与已提交结论矛盾即 fail-closed

### 6.2 知识库（补充通道）

- 导出 = 规则卡原文 + 元数据头，一字不改；双跑字节一致
- 三状态显式（ACTIVE/PENDING_HUMAN_REVIEW/RETIRED）
- 台账：KB ID ↔ package_checksum ↔ 阈值；阈值 0.60 机器化进导出清单
- 重发布流程：导出→上传→带 0.60→重验收→记账

### 6.3 冻结数据

- 考卷 8 / 路由 50 / 检索 12 / plan_a 夹具；amendment 须文档化
- 敏感面：denied-markers 入验证器；诊断字段仅结构 ID；运行记录为脱敏重构版并标注来源

## 7. 安全规范

### 7.1 fail-closed 与不回显

任一门禁失败 → 固定文案回退；拒绝路径不回显伪造/不可信值；对外只暴露安全类别。

### 7.2 路由安全

硬拒（提示词窃取/忽略防护/外传凭据/what-if）先于锚定模板，锚定先于分类器；弃权/越界/低置信均安全回退。

### 7.3 质检通道与预算

content 必须为空、工具调用恰一次；binding 校验 digest/allowlist/动作语义；每候选每角色 ≤2、门卫 ≤1、总量账本封顶。

### 7.4 敏感信息

env 外置不入库；网页不显示密钥；镜像/部署物不含密钥。

## 8. 开发规范

### 8.1 本地开发

```bash
uv run python -m streamlit run app.py        # 演示（dry-run 默认）
uv run python -m pytest tests -q             # 离线守护（勿用 uv run pytest：启动器被应用控制策略拦截）
uv run python scripts/run_reviewer_judgment_eval_v14.py        # 评测彩排
uv run python scripts/run_reviewer_judgment_eval_v14.py --real # 真实跑（需 env + 批准）
```

### 8.2 代码质量

- 角色模块逐版继承 `*_vN.py`；不原地改
- 自证监考：变异/负例/伪造桩/零 provider 桩
- 571 通过/1 跳过（tests/ 口径）

### 8.3 构建与验收命令

| 命令 | 说明 |
|------|------|
| `uv run python -m pytest tests -q` | 离线回归 |
| 各脚本默认 | 彩排，零调用 |
| `--real` | 真实跑，预算封顶、逐轮批准 |
| `scripts/export_knowledge_base_v1.py --write` | 重新生成导出快照 |

## 9. 监控与维护

### 9.1 日志

systemd journal（结构化日志/指标为非目标，留待运维阶段）。

### 9.2 健康检查

冒烟三件（5.5）；systemd 自动重启。

### 9.3 维护台账与触发器

台账 `docs/knowledge-base-publications.md`；触发器：R5 转正重发布、ECS 首跑、分诊升级补准确率、UI 异常分支补演习、canonical/Hannah 合并复验 O-6、ECS 续费（2026-09-12 前，约 09-05 定）。

## 10. 性能优化

### 10.1 成本

预算公式 max_provider_calls = 4*(rounds+1)+triage；评测 ledger = 2×案例数；真实调用逐轮批准（全周期约 45 次/约 15 万 tokens，自报口径，无逐次台账）。

### 10.2 延迟

质检员 temperature=0、stream=False、enable_thinking=False；单轮单调用，无链式展开。

### 10.3 检索

TopK 50/50 + qwen3-rerank；阈值 0.60 平衡污染与召回。

## 11. 兼容性说明

### 11.1 运行环境

ECS Ubuntu 22.04（上海）跨区调北京百炼，预期可行，以 O-7 首跑为准；浏览器现代版本即可（Streamlit）。

### 11.2 开发环境

Windows 本地：git push 走代理（代理未启报连接错）；`uv run pytest` 启动器被应用控制策略拦截，用 `python -m pytest` 替代。

### 11.3 版本兼容

Python 3.14；Streamlit 1.61.x；契约 schema_version 恒 "1.0"。

## 12. 故障排除

### 12.1 常见问题

#### 真实跑全 CONFIG/0 tokens
- 该终端缺环境变量；设好 DASHSCOPE_API_KEY/DASHSCOPE_WORKSPACE_ID 重跑（零消耗，非回归）

#### push 报 Could not connect
- 代理客户端未运行；启动后重试（与评测无关）

#### pytest 报 Application Control policy blocked
- 换 `uv run python -m pytest`

#### 工作区根校验红（PackageDriftError）
- O-6 已记录；运行时用 bundle 根（AD-3）；待合并复验至 MANIFEST_OK

#### 网页报"发布清单匹配不唯一"
- AD-3 唯一匹配 fail-closed 生效；联系维护者核对台账

### 12.2 调试命令

```bash
journalctl -u <service> -f          # 服务日志
systemctl restart <service>         # 重启
uv run python -m pytest tests -q    # 离线回归
uv run python scripts/run_three_role_e2e_v15.py   # E2E 彩排
```

---
*文档版本: 1.0*
*最后更新: 2026-08-14*
