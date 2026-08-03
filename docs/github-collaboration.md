# GitHub 团队协作与发布指南

## 1. 推荐结论

GitHub 可以统一承载团队可下载、可复现的本体卡片、大模型接入代码、Prompt、Schema、合成 Fixture、测试和当前文档。首版使用 Private 仓库，建议名称为 `campaign-optimizer-review`；确认知识产权、客户数据和第三方资料边界后，再决定是否公开。

## 2. 当前仓库审计

截至 2026-08-03：

- 未配置 Git remote；当前分支为 `master`；
- `git ls-files` 为 0，没有任何已提交文件；
- 所有项目文件都是 untracked；
- 文件名级秘密扫描未确认发现真实 Key，但不能据此认定安全；
- 当前离线回归为 `277 passed`；
- `_bmad-output` 包含历史路线和内部产物，不应整体上传。

因此禁止直接执行未经审查的 `git add .`。

## 3. 首版允许上传

- `campaign_optimizer/`：运行代码、Schema、权威公开规则投影；
- `scripts/`：本地检查、smoke test 和可复现实验脚本；
- `tests/`：单测、集成测试、合成或脱敏 Fixture、评测集；
- `docs/`：当前架构、安装、安全、进度和 ADR；
- `.github/workflows/`、Issue 模板和 PR 模板；
- `README.md`、`CONTRIBUTING.md`、`.gitignore`、`.env.example`；
- `pyproject.toml`、`uv.lock`、`.python-version`。

## 4. 禁止或默认排除

绝对禁止：

- Qwen/DashScope API Key、阿里云 AccessKey、Token、Cookie；
- `.env` 和任何真实凭据文件；
- 客户原始广告数据、真实用户 ID、真实会话与业务日志；
- 未脱敏截图、请求头和完整线上响应；
- 私有数据库、模型文件和未经授权的第三方资料。

默认排除：

- `.agents/`、`.claude/`、`.codex/`、`.github/agents/`；
- `_bmad/` 与全量 `_bmad-output/`；
- 虚拟环境、缓存、coverage、日志和 SQLite；
- ZIP 交接包、IDE workspace 和临时文件；
- 未确认再分发许可的第三方 API 规范。

历史 BMAD 产出中仍有效的内容应整理到 `docs/`，而不是提交整个生成目录。

## 5. 推荐目录

```text
campaign-optimizer-llm-integration/
├─ campaign_optimizer/
│  ├─ contracts/
│  ├─ schemas/
│  ├─ ontology/
│  └─ llm/
│     ├─ client.py
│     ├─ config.py
│     ├─ orchestrator.py
│     ├─ prompts.py
│     ├─ retriever.py
│     └─ fallback.py
├─ scripts/
├─ tests/
│  ├─ fixtures/
│  ├─ integration/
│  └─ evals/
├─ docs/
│  ├─ architecture/
│  ├─ decisions/
│  ├─ local-setup.md
│  ├─ progress.md
│  └─ security.md
├─ .github/
│  ├─ workflows/
│  ├─ ISSUE_TEMPLATE/
│  └─ pull_request_template.md
├─ .env.example
├─ .gitignore
├─ CONTRIBUTING.md
├─ README.md
├─ pyproject.toml
└─ uv.lock
```

## 6. 首次提交

先确定 GitHub Owner/Organization、Private 仓库名和可上传的规则资料。当前项目使用 `uv` 管理 Python 3.14 和锁定依赖，采用白名单式暂存：

```powershell
git branch -M main
git add README.md .gitignore .env.example pyproject.toml uv.lock .python-version
git add campaign_optimizer scripts tests docs
git diff --cached --stat
git diff --cached
```

人工审查与秘密扫描通过后才能 commit。创建远程仓库后再执行：

```powershell
git remote add origin <private-repository-url>
git push -u origin main
```

远程仓库当前尚未创建，因此本文不假设 URL，也不执行外部推送。

## 7. 团队流程

GitHub Project 推荐：

```text
Backlog → Ready → In Progress → In Review → Done
```

Issue 类型使用 `task`、`experiment`、`bug`、`decision` 和 `security`。每个 Issue 至少包含背景、输入 Fixture、验收标准、是否调用付费 API、结果证据和不在范围内的事项。

Pull Request 规则：

- 一个 PR 对应一个 Issue；
- 禁止直接推送 `main`；
- 至少一名成员 Review；
- 单元测试、Schema Gate 和秘密扫描通过；
- 真实 Qwen 测试默认不随每个 PR 自动运行，使用手动触发；
- PR 只提交脱敏请求、request ID、耗时、判定和结果摘要。

GitHub Issues 可关联负责人、子任务、依赖和 PR；Projects 可统一显示状态。GitHub Actions Secret 可保存手动集成测试所需的 Key，但 Secret 仍不得进入代码或日志。

## 8. CI 与 Release

普通 CI 不需要 Qwen Key，只运行离线测试：

```text
uv sync --frozen
uv run pytest -q
```

真实 API 测试使用单独的手动 workflow，并将 `DASHSCOPE_API_KEY` 限制在明确需要它的 environment/workflow。

Release 计划：

- `v0.1.0-contracts`：契约、Fixture 和离线 Gate 全绿；
- `v0.2.0-qwen-client`：QwenClient、mock 与手动 smoke test；
- `v0.3.0-local-orchestrator`：本地纵向切片和 Retriever 接口；
- `v0.4.0-local-e2e`：UI、本地 E2E 和至少两名成员复现；
- `v0.5.0-retrieval-poc`：可选百炼知识库或本地向量检索对比；
- `v0.6.0-deployment-preview`：确需远程访问时的 ECS 预览。

每个 Release 记录模型、测试通过数、已知限制、本地复现命令、是否产生 API 费用和评测摘要。

## 9. 创建远程仓库前的必要决定

1. GitHub Owner/Organization；
2. 仓库名称；
3. Private 或 Public（推荐 Private）；
4. 哪些成员拥有 Write/Admin 权限；
5. 团队 Python 版本；
6. 是否允许上传本体规则卡及第三方 API 规范。
