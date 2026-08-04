# Review-only 运行时数据库 v2

**状态：当前权威实现边界**  
**日期：2026-08-04**

## 决策

- Git 发布包是概念卡、规则卡、护栏和反馈策略的唯一权威来源。
- SQLite 是本地开发和离线测试替身；PolarDB for PostgreSQL 是 Demo/部署目标。
- 数据库只保存上游产物、最终方案、确定性本体评价、用户反馈、运行时置信度和审计记录。
- `INITIAL_SEED / is_optimized=false` 等中间输出只能进入 `model_artifacts`，不得直接触发本体 Review。
- 只有通过 `final_plan` 契约的最终方案才能进入 `plan_snapshots`。
- 历史方案、评价和反馈不可覆盖；更正使用新的事件或快照。

## 首批运行时表

| 表 | 用途 |
|---|---|
| `model_artifacts` | 原样保存MTA及其他小模型中间/最终产物和追溯信息 |
| `plan_snapshots` | 一次不可变最终方案快照 |
| `plan_items` | 最终方案内逐渠道/逐Campaign条目 |
| `ontology_reviews` | 一次确定性本体评价及总体结论 |
| `ontology_review_items` | 每个方案条目对应的规则评价 |
| `feedback_events` | GOOD/FINE/BAD不可变反馈事件及摘要 |
| `rule_confidence_states` | 每客户、每规则版本的当前运行时置信度投影 |
| `plan_decision_events` | 用户对方案的ACCEPT/REJECT事件 |

所有客户内数据均包含 `client_id`。业务主键在客户范围内唯一，客户内外键必须同时包含 `client_id`，防止跨客户关联。

## 写入顺序

```text
model artifact
  → final plan snapshot + items
  → ontology review + items
  → feedback/decision events
  → transactional confidence projection
```

反馈事件写入和置信度更新必须处于同一事务；相同 `feedback_id` 和相同payload为幂等重放，相同ID但payload不同必须拒绝。

## 迁移策略

1. 保留旧 `concepts/rules/clients/diagnoses/execution_log`，不做破坏性转换。
2. 使用 Alembic 前向迁移新增运行时表。
3. 本地先通过SQLite契约测试。
4. PolarDB环境必须额外验证JSONB、复合外键、行锁、并发反馈、连接池、TLS和迁移回退策略。
5. 未完成真实PolarDB烟测前，不得宣称生产数据库能力已完成。

本地迁移默认连接 `sqlite:///ontology-runtime.db`。指定其他SQLite数据库或PolarDB时，必须显式设置：

```powershell
$env:ONTOLOGY_DATABASE_URL='sqlite:///ontology-runtime.db'
uv run alembic upgrade head
```

运行时数据完整性要求：

- SQLite反馈写事务使用 `BEGIN IMMEDIATE`，防止并发反馈丢失更新；PostgreSQL路径使用状态行锁；
- feedback ID重复且摘要一致返回 `ALREADY_APPLIED`，摘要不同则拒绝；
- 模型产物、方案、评价、评价条目、反馈和方案决策是不可变快照/事件；
- 摘要由规范JSON生成并在写入时核验；
- 事件发生时间与服务器接收时间分开保存，运行时状态使用服务器UTC时间；
- `drop_first`只允许SQLite，不得用于PostgreSQL/PolarDB。
