# SQLite → 阿里云 PostgreSQL 换库演练 Runbook(本体侧审查系统)

- 适用对象:Hannah 集成分支(`.worktrees/hannah-convergence/`)的审查运行时 + Ontology Review API。
- 占位符约定:`$PG_DRILL_URL` = 演练用 PG 连接串(形如 `postgresql://user:***@host:5432/dbname?sslmode=require`)。Windows cmd 下把 `export VAR=...` 换成 `set VAR=...`,引用写成 `%VAR%`。
- 全部命令里的凭据只出现在环境变量里。**不要**把连接串粘进聊天、截图、commit、日志。

---

## 0. 凭据安全存放

```bash
# 在仓库根目录创建 .env.pg(该文件已被 .gitignore 的 .env.* 规则覆盖,不会入 git)
cat > .env.pg <<'EOF'
PG_DRILL_URL=postgresql://<user>:***@<host>:5432/<dbname>?sslmode=require
EOF
chmod 600 .env.pg            # ECS/Linux 上;Windows 上确保只有本人可读
set -a; source .env.pg; set +a   # 当前会话加载
```

**为什么**:凭据一旦进入 git 历史、聊天记录或截图就视同泄露,只能轮换;环境变量 + 本地忽略文件是唯一可接受的载体。

---

## 1. 探路:连通性 + 建表权限 + SSL

```bash
uv run --with psycopg2-binary python -c "import os, psycopg2; \
c = psycopg2.connect(os.environ['PG_DRILL_URL'], connect_timeout=10); \
cur = c.cursor(); cur.execute('SELECT 1'); print('SELECT 1 =', cur.fetchone()); \
cur.execute('CREATE TEMP TABLE drill_probe (x int)'); cur.execute('INSERT INTO drill_probe VALUES (1)'); \
print('临时表读写 OK'); c.close()"
```

**为什么**:在做任何迁移之前,先证明网络(含阿里云白名单)、账号、SSL(`sslmode=require`)与建表权限都通;失败在这一步排查成本最低。

---

## 2. live-Postgres 测试转绿

测试文件:`.worktrees/hannah-convergence/ontology review api/tests/test_postgres_integration.py`
读取的环境变量:**`TEST_POSTGRES_URL`**(必须 `postgresql+psycopg://` 方案、库名以 `_test` 结尾)+ 确认开关 **`ALLOW_POSTGRES_TEST_MIGRATION=1`**。

```bash
cd ".worktrees/hannah-convergence/ontology review api"
export TEST_POSTGRES_URL="postgresql+psycopg://<user>:***@<host>:5432/<dbname>_test?sslmode=require&connect_timeout=10"
export ALLOW_POSTGRES_TEST_MIGRATION=1
uv run --extra test python -m pytest tests/test_postgres_integration.py -q
```

注意:该项目锁定的是 psycopg3(`psycopg[binary]==3.3.4`),测试还会断言驱动名为 `psycopg`,所以这里用项目自带依赖(`--extra test` 补 pytest/httpx),**不是** `--with psycopg2-binary`。

**为什么**:这是团队已有的、覆盖"迁移 + 持久化 + 幂等重放 + 重启后读回"的验收测试;它不绿,后面一切免谈。测试用独立 `_test` 库并自带清理,不污染演练库。

---

## 3. alembic upgrade head 对演练库建表

alembic 位置:worktree 根目录 `.worktrees/hannah-convergence/alembic.ini` + `migrations/`(versions:`da19a197a9f7` 建 8 张运行时表,`7b8f3d1a2c4e` 补 revision/release 列)。连接串通过 **`ONTOLOGY_DATABASE_URL`** 环境变量注入(`migrations/env.py` 读取)。

关键前提:迁移只建 8 张运行时表(model_artifacts / plan_snapshots / plan_items / ontology_reviews / ontology_review_items / feedback_events / rule_confidence_states / plan_decision_events),且第一个迁移就声明了指向 `clients.client_id` 的外键——五张基础表(concepts / rules / clients / diagnoses / execution_log)必须先由 `create_all` 建好(与 `tests/test_runtime_migration.py` 的模式一致):

```bash
cd .worktrees/hannah-convergence
# 3a. 先建五张基础表(幂等:create_all 只补缺)
uv run --with psycopg2-binary python -c "import os; \
from campaign_optimizer.ontology.db import Base, ConceptRow, RuleRow, ClientRow, DiagnosisRow, ExecutionLogRow; \
from sqlalchemy import create_engine; \
e = create_engine(os.environ['PG_DRILL_URL']); \
Base.metadata.create_all(e, tables=[ConceptRow.__table__, RuleRow.__table__, ClientRow.__table__, DiagnosisRow.__table__, ExecutionLogRow.__table__]); \
print('legacy 5 tables ready')"
# 3b. 再跑 alembic
# 注意:alembic 的 configparser 会把连接串里的 % 当插值符(密码含 %40 等转义会崩)。
# 解法:密码改走 PGPASSWORD 环境变量,连接串不含密码与 %:
#   export PGPASSWORD='<真实密码>'
#   export ONTOLOGY_DATABASE_URL="postgresql://<user>@<host>:5432/<dbname>?sslmode=prefer&connect_timeout=10"
export ONTOLOGY_DATABASE_URL="$PG_DRILL_URL"
uv run alembic upgrade head
uv run alembic current        # 应显示 7b8f3d1a2c4e (head)
```

**为什么**:目标表结构必须先于数据搬迁存在;基础表先行是因为迁移脚本的外键依赖,顺序颠倒会在 PG 上直接建表失败。

---

## 4. 停写 → 搬迁 → 行数对账

```bash
# 4a. 停掉一切会写源 SQLite 的服务(按实际部署方式,示例为 systemd)
sudo systemctl stop <review-service>
# 4b. 搬迁(脚本只读源库;--yes 表示已确认停写)
uv run --with psycopg2-binary python scripts/drill_migrate_sqlite_to_pg.py \
    --sqlite <path/to/ontology-runtime.db> --yes
```

脚本行为:按外键拓扑序逐表 INSERT(自引用表父行先行),按源/目标列名交集复制,结束打印每张表 `source / inserted / target` 行数对账;任一行 MISMATCH 或类型错误会立即报错退出。

**为什么**:搬迁期间若有写入,新行会丢失或两边分叉,对账数字将不可信;先停写是把"搬完即一致"变成可验证命题。

---

## 5. 双跑脚本(灵魂步)

```bash
uv run --with psycopg2-binary python scripts/drill_cross_db_double_run.py
```

脚本用同一份种子 plan(`tests/fixtures/plan_a/final_plan.demo.json`)与同一个固定 release,在临时 SQLite 与 `$PG_DRILL_URL` 上各跑一遍真实 workflow(`review_final_plan` → 幂等重放 → `rereview`),读回持久化状态,比对 review_id、overall_verdict、完整 review payload 与四张持久化表的行哈希(墙钟 `created_at` 排除在外)。输出 PASS/FAIL 与差异明细。

**为什么**:行数对账只证明"数据搬过去了",双跑才证明"同一输入在新库上跑出同一结论"——review_id 是确定性摘要,任何 schema、约束或 JSONB 往返差异都会让它分叉。**不过不切换。**

---

## 6. 切换配置 + 冒烟三件套 + SQLite 保留两周

```bash
# 6a. 把服务指向 PG(API 侧读 DATABASE_URL;本体运行时读 ONTOLOGY_DATABASE_URL)
# systemd 单元中:
#   Environment=DATABASE_URL=$PG_DRILL_URL
#   Environment=ONTOLOGY_DATABASE_URL=$PG_DRILL_URL
sudo systemctl daemon-reload && sudo systemctl start <review-service>
# 6b. 冒烟三件套
curl -sf "$SERVICE_URL/ready"                                     # ① 健康检查 200
curl -sf -X POST "$SERVICE_URL/api/v1/plan-reviews" \
     -H "X-API-Key: $API_KEY" -H "Idempotency-Key: drill-smoke-1" \
     -H "Content-Type: application/json" \
     --data @".worktrees/hannah-convergence/tests/fixtures/plan_a/final_plan.demo.json"  # ② 创建 201
#    用同一 Idempotency-Key 重放同一请求,必须返回同一个 review_id
curl -sf "$SERVICE_URL/api/v1/plan-reviews/<review_id>" -H "X-API-Key: $API_KEY"         # ③ 读回一致
# 6c. 旧库保留:改名存档,两周后再删
mv <path/to/ontology-runtime.db> <path/to/ontology-runtime.db.pre-pg-cutover.$(date +%F)>
```

**为什么**:配置切换是回滚成本最低的一步——冒烟三件套验证"服务在 PG 上真的能干活",而保留两周的 SQLite 文件是不依赖 PG 备份的快速回滚兜底。

---

## 7. 台账记录

在 `docs/operations/pg-cutover-ledger.md` 追加一行(文件不存在则新建):

```markdown
| 日期 | 操作人 | 源库 | 目标库 | 对账 | 双跑 | 冒烟 | 备注 |
|------|--------|------|--------|------|------|------|------|
| 2026-08-12 | <姓名> | ontology-runtime.db | <dbname>@<host> | 13 表 OK | PASS | 3/3 | 演练/正式 |
```

**为什么**:换库是跨会话事件,口头与聊天记录都会过期;一行台账让下一个值班人十秒钟知道"现在主库是哪个、谁切的、能不能回滚"。
