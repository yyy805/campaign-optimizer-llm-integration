# 团队协作规则

## 首次准备

```powershell
git clone <repository-url>
cd campaign-optimizer-review
uv sync --frozen
uv run pytest -q
```

## 日常更新

开始工作前先同步 `main`，再创建自己的分支：

```powershell
git switch main
git pull --ff-only
git switch -c ontology/<short-task-name>
```

完成后运行：

```powershell
uv run python scripts/check_ontology_package.py --project-root .
uv run pytest -q
git add <明确的文件路径>
git commit -m "ontology: describe the change"
git push -u origin HEAD
```

随后在 GitHub 创建 Pull Request。不要直接向 `main` 推送，也不要使用 `git add .`；应明确选择本次任务涉及的文件。

## 冲突处理

如果 `git pull --ff-only` 提示不能快进，先保留自己的修改并联系另一位成员确认重叠卡片。规则卡和概念卡发生冲突时，不要简单保留两边内容；必须检查字段语义、版本历史、规则引用和测试，再由 Pull Request 评审决定。

## 禁止提交

- `.env`、API Key、Token 和账号凭据；
- 客户原始数据、真实用户标识、业务日志和未脱敏截图；
- `_bmad/`、`_bmad-output/`、`.agents/`、IDE workspace、ZIP 交接包；
- 未确认再分发许可的第三方资料。
