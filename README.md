# Campaign Optimizer

本仓库保存投放优化 Demo 的本体卡片、review-only 契约、大模型交换契约及自动检查。

## 当前权威文档

- [本体Review-only权威架构v2](docs/architecture/ontology-review-only-v2.md)
- [本体搭建执行计划v2](docs/ontology-build-plan-v2.md)
- [本次架构纠偏记录](docs/decisions/sprint-change-proposal-2026-08-03.md)

`_bmad-output` 中的早期PRD、架构、Epic和搭建计划仅作历史记录；与以上v2文档冲突时，以v2为准。

## 本地环境

```powershell
uv sync
uv run pytest -q
```

`uv` 会按 `pyproject.toml` 和 `uv.lock` 建立一致的 Python 环境，不需要手工激活虚拟环境。

## 本体维护检查

概念卡批量检查（Schema、概念间引用、规则引用的概念是否存在）：

```powershell
uv run python scripts/check_ontology_package.py --project-root .
```

MTA 文件检查不扫描电脑，只读取显式传入的 MTA 仓库根目录，并依据固定 manifest 检查 8 份 CSV 是否存在、必需字段是否齐全、是否至少有一行数据：

```powershell
uv run python scripts/check_mta_data.py `
  --project-root . `
  --mta-root "D:\AAA Data science and AI for busines\Github\marketing-roi-analysis"
```

部署到 ECS 后仍运行同一个脚本，只需把路径改成服务器挂载路径；也可设置 `CAMPAIGN_OPTIMIZER_PROJECT_ROOT` 和 `CAMPAIGN_OPTIMIZER_MTA_ROOT` 环境变量，由部署任务、定时任务或 CI 调用。

检查一组小模型最终方案、本体评价和 LLM 上下文：

```powershell
uv run python scripts/check_review_contract.py `
  --plan tests/fixtures/plan_a/final_plan.demo.json `
  --review tests/fixtures/plan_a/ontology_review.demo.json `
  --context tests/fixtures/plan_a/llm_context.demo.json
```

## 反馈边界

- 方案只记录 `ACCEPT/REJECT`，使用 `plan_decision_event.schema.json`。
- 本体评价只记录 `GOOD/FINE/BAD`，使用 `feedback_event.schema.json`。
- 反馈只更新独立的运行时 `confidence_state`，不会直接修改规则卡，也不会自动创建新规则。
- `feedback_policy.demo.json` 的增减幅度和阈值仅用于跑通 Demo，生产部署前必须重新标定并经人工审批。

## 规则卡 review-only 约定

- 本体只审核小模型链路最终方案，不生成、修改或自动执行方案。
- `trigger_condition` 与 `match_inputs` 决定规则需要检查哪些公开事实。
- `review_policy` 把已命中规则与最终方案动作映射为 `SUPPORT/CONFLICT/NOT_APPLICABLE`；没有规则覆盖时才使用 `UNVERIFIED`。
- `reference_action` 只记录规则设计背景，不是可执行接口；本体评价只能由 `review_policy` 产生。
- `confidence_model.thresholds` 只区分高置信和最低可用置信度，不包含自动执行阈值。
- R5 当前为 `ACTIVE / 1.3-contract-hardening`，10%、20%、5%均是 Demo 占位阈值。
- R7 当前为 `RETIRED / 1.3-contract-hardening`；没有真实预测模块前只能形成未覆盖评价。

## GitHub 协作

团队成员不再通过聊天软件互发文件。首次克隆和日常协作命令见
[`docs/github-collaboration.md`](docs/github-collaboration.md)。所有改动通过独立分支和 Pull Request 合并，`main` 只保存已通过离线检查的最新版。
