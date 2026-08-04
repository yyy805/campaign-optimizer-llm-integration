# Ontology Review API：RAM 账号部署与验证操作手册

本文给负责部署的组员使用。目标是：组员使用现有阿里云 RAM 账号创建一台 ECS，在 ECS 上部署本仓库的 Ontology Review API，并完成平台冒烟测试、数据持久化测试和 Campaign Optimizer `final_plan` 业务联调。

本文第一轮验收使用 Docker Compose、单实例 API 和 SQLite 持久卷，不要求先购买 PolarDB。等基础验收通过后，再决定是否切换到 PolarDB PostgreSQL。

---

## 1. 最终要交付什么

完成后，部署负责人需要向团队提交以下信息：

- ECS 地域、实例 ID、操作系统和私网 IP；
- API 访问地址，例如 `http://<ECS_IP>:8000`；
- Docker 镜像版本 `ontology-review-api:0.1.0`；
- `/health` 和 `/ready` 的验证结果；
- `MATCH`、`CONFLICT`、`NO_COVERAGE` 三类 Review 的验证结果；
- Campaign Optimizer `final_plan` 联调结果；
- 容器重启后的数据持久化和幂等验证结果；
- 测试时间、执行人、Review ID、Request ID、Ontology 版本及 checksum；
- 未通过项目及相关日志。

API Key、ECS 登录密码、私钥和数据库密码不得发到群里或写入验收报告。

## 2. 角色分工

### 阿里云主账号管理员

主账号管理员负责：

1. 完成阿里云实名认证并保证余额或额度充足；
2. 为 RAM 用户开启控制台访问；
3. 给 RAM 用户授予 ECS 权限；
4. 指定允许使用的地域、实例规格和预算；
5. 必要时提前创建 VPC、交换机和安全组。

建议授予 RAM 用户：

```text
AliyunECSFullAccess
```

如果 RAM 用户需要自行创建 VPC 和交换机，再授予：

```text
AliyunVPCFullAccess
```

如果购买包年包月实例，还需要：

```text
AliyunBSSOrderAccess
```

本次短期验证建议使用按量付费。不要给普通组员授予 `AdministratorAccess` 或 `AliyunRAMFullAccess`。

### RAM 用户/部署负责人

RAM 用户负责：

1. 登录阿里云 RAM 控制台；
2. 创建并配置 ECS；
3. 在 ECS 安装 Docker；
4. 把完整项目文件放到 ECS；
5. 创建服务器专用 `.env`；
6. 启动 API 并完成全部验证；
7. 提交验收记录；
8. 验证结束后按团队决定保留或释放资源。

## 3. 开始前检查

部署负责人开始前必须拿到：

- RAM 专属登录地址；
- RAM 用户名和初始密码；
- 主账号管理员确认的权限；
- 允许使用的阿里云地域和预算；
- 完整的项目目录；
- 团队指定的 API Key，或者获准自行生成一条测试 Key。

必须复制完整项目根目录，不能只复制 `ontology review api` 文件夹。Docker 构建还会读取同级 `docs` 中的 canonical ontology、`final_plan` schema 和 `ontology_review` schema。

项目结构至少应包含：

```text
bmad/
├── ontology review api/
│   ├── Dockerfile
│   ├── docker-compose.yml
│   ├── .env.example
│   ├── app/
│   ├── migrations/
│   ├── scripts/
│   └── tests/
└── docs/
    ├── ontology/
    └── campaign-optimizer-llm-integration-main/
```

## 4. 在组员电脑上先做本地基线验证

这一步在部署负责人的电脑执行，用来确认收到的代码包本身可以运行。若组员电脑暂时没有 Python 3.12，可以先跳到 ECS 部署，但最终必须保留 ECS 上的验证结果。

进入 API 目录：

```bash
cd "<项目根目录>/ontology review api"
```

创建 Python 环境并安装依赖：

```bash
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[test]'
```

执行自动化测试：

```bash
python -m pytest -rA
```

当前仓库的参考基线是：

```text
77 passed, 1 skipped
```

跳过项是未配置临时 PostgreSQL/PolarDB 数据库时的集成测试。只要其他测试全部通过，SQLite 第一阶段部署可以继续。

如果测试失败，先保存完整输出，不要带着失败基线继续部署 ECS。

## 5. RAM 用户创建 ECS

使用管理员提供的 RAM 专属地址登录，然后进入“云服务器 ECS → 实例 → 创建实例”。建议配置如下：

| 配置 | 第一阶段建议 |
| --- | --- |
| 付费方式 | 按量付费 |
| 地域 | 团队指定地域 |
| 操作系统 | Ubuntu 22.04 或 24.04 64 位 |
| 实例规格 | 至少 2 vCPU、2 GiB；建议 2 vCPU、4 GiB |
| 系统盘 | ESSD，40 GiB 或以上 |
| VPC/交换机 | 使用团队指定资源 |
| 公网 IP | 需要从组员电脑访问时开启 |
| 公网带宽 | 测试阶段 1～5 Mbps |
| 登录方式 | SSH 密钥对优先 |
| 实例名称 | `ontology-review-api-test` |

### 安全组

建议规则：

| 端口 | 来源 | 用途 |
| --- | --- | --- |
| TCP 22 | 部署负责人公网 IP `/32` | SSH 登录 |
| TCP 8000 | 团队测试电脑或 Agent 所在网络 | API 验证 |

不要把 SSH 22 长期开放给 `0.0.0.0/0`。端口 8000 也只向获准的测试来源开放；正式环境应在前面增加 HTTPS 反向代理，而不是长期直接暴露 HTTP 8000。

第一阶段使用 SQLite，不需要开放 3306、5432 或其他数据库端口。

## 6. 登录 ECS 并安装运行环境

在组员电脑执行：

```bash
ssh <ECS用户名>@<ECS公网IP>
```

用户名以创建实例时的选择为准，常见为 `root` 或 `ecs-user`。

更新系统并安装基础工具：

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl git docker.io docker-compose-v2
```

如果系统仓库没有 `docker-compose-v2`，按照 Docker 官方 Ubuntu 安装文档安装 Docker Engine 和 Compose plugin，不要安装来源不明的脚本。

启动 Docker：

```bash
sudo systemctl enable --now docker
sudo docker version
sudo docker compose version
```

为了避免修改用户组后重新登录，本文后续统一使用 `sudo docker`。

## 7. 将项目放到 ECS

可以使用团队代码仓库拉取，也可以从部署负责人的电脑上传。无论采用哪种方式，都必须传完整项目根目录。

示例：从组员电脑上传整个项目目录：

```bash
scp -r "<本地项目根目录>" <ECS用户名>@<ECS公网IP>:~/
```

上面的本地项目根目录应命名为 `bmad`，上传后远端路径为 `~/bmad`。

上传后在 ECS 检查关键文件：

```bash
cd ~/bmad
test -f "ontology review api/docker-compose.yml"
test -f "ontology review api/Dockerfile"
test -d "docs/ontology/ontology 概念卡"
test -f "docs/campaign-optimizer-llm-integration-main/campaign_optimizer/schemas/final_plan.schema.json"
```

以上命令都没有报错才继续。

## 8. 创建服务器专用配置

进入 API 目录：

```bash
cd ~/bmad/"ontology review api"
cp .env.example .env
chmod 600 .env
```

编辑 `.env`：

```bash
nano .env
```

第一阶段建议保留 SQLite：

```dotenv
APP_ENV=demo
DATABASE_URL=sqlite:////data/review.db
ONTOLOGY_PATH=/ontology
EXPECTED_ONTOLOGY_CHECKSUM=a2eaaf287417469a592ecb48d3a31759f930761bc97269e9c61618b7f65ca858
FINAL_PLAN_SCHEMA_PATH=/contracts/final_plan.schema.json
ONTOLOGY_REVIEW_SCHEMA_PATH=/contracts/ontology_review.schema.json
DOCS_ENABLED=true
CORS_ORIGINS=[]
LOG_LEVEL=INFO
API_KEY_PRINCIPALS=<实际测试Key>:demo-agent:demo:SERVICE,<实际ReviewerKey>:demo-reviewer:demo:REVIEWER
PLAN_REVIEW_CLIENT_ID=demo_client_001
```

注意：

- 把所有 `change-me` 值替换掉；
- API Key 建议使用密码生成器生成，不要使用姓名、手机号或简单字符串；
- Key 格式为 `key:principal_id:tenant:role`，多条记录以逗号分隔；
- 当前支持的角色配置以项目为准，测试调用使用 `SERVICE` Key；
- `.env` 不得提交到 Git，也不得发送到群聊；
- `EXPECTED_ONTOLOGY_CHECKSUM` 用于锁定经过验证的规则快照，不要随意修改；
- 如果不需要网页 Swagger，完成调试后将 `DOCS_ENABLED` 改为 `false`。

本文后续把 `SERVICE` 对应的实际 Key 写作 `<SERVICE_API_KEY>`。

## 9. 构建并启动 API

必须在 `ontology review api` 目录中执行 Compose 命令：

```bash
cd ~/bmad/"ontology review api"
sudo docker compose config
sudo docker compose build
sudo docker compose up -d
sudo docker compose ps
```

查看启动日志：

```bash
sudo docker compose logs --tail=200 review-api
```

期望结果：

- `review-api` 容器处于运行状态；
- 宿主机端口 8000 映射到容器端口 8000；
- 启动时 Alembic migration 成功；
- `/data` 使用 Compose 持久卷；
- `/ontology` 以只读方式挂载；
- 日志中没有数据库、ontology checksum 或 schema 加载错误。

如果容器没有启动，不要继续做 API 验收。先执行：

```bash
sudo docker compose ps
sudo docker compose logs --tail=300 review-api
```

## 10. 第一层验收：运行状态

在 ECS 内执行：

```bash
curl --fail --show-error http://127.0.0.1:8000/health
curl --fail --show-error http://127.0.0.1:8000/ready
```

`/health` 期望返回：

```json
{"status":"alive"}
```

`/ready` 必须返回 HTTP 200，并确认以下四项均已准备好：

- ontology；
- database；
- API Key principals；
- external contracts。

同时检查返回的 ontology 版本和规则集合。当前测试基线应包含 `v1.1-demo` 以及 `R1` 至 `R7`。

重要：`/health` 成功只代表进程存活。只要 `/ready` 不是 200，本次发布就不能判定通过，业务写入也不应继续。

## 11. 第二层验收：Review API 冒烟测试

仓库已经提供 `scripts/smoke.sh`。在 ECS 的 API 目录执行：

```bash
SMOKE_API_KEY='<SERVICE_API_KEY>' ./scripts/smoke.sh
```

脚本会自动验证：

1. `GET /health`；
2. `GET /ready`；
3. `POST /api/v1/reviews` 返回 `MATCH`；
4. `POST /api/v1/reviews` 返回 `CONFLICT`；
5. `POST /api/v1/reviews` 返回 `NO_COVERAGE`；
6. 返回体中存在有效 `review_id`；
7. `X-API-Key` 和 `Idempotency-Key` 生效。

期望最后看到：

```text
Smoke test passed: health, readiness, MATCH, CONFLICT, NO_COVERAGE
```

三个结果的业务含义：

| 结果 | 含义 |
| --- | --- |
| `MATCH` | 已知且有效的 ontology 规则支持当前输入 |
| `CONFLICT` | 输入或建议与 canonical 规则冲突 |
| `NO_COVERAGE` | 当前范围没有适用的有效规则，不能伪造匹配结果 |

## 12. 从组员电脑验证 ECS 地址

在部署负责人的电脑进入同一份项目的 API 目录，执行：

```bash
cd "<本地项目根目录>/ontology review api"
BASE_URL='http://<ECS公网IP>:8000' \
SMOKE_API_KEY='<SERVICE_API_KEY>' \
./scripts/smoke.sh
```

如果 ECS 内部冒烟通过，但远程失败，优先检查：

- ECS 是否有公网 IP；
- 安全组 TCP 8000 是否允许部署负责人当前公网 IP；
- 宿主机防火墙是否阻止 8000；
- `docker compose ps` 是否显示 `8000:8000`；
- 运营商或公司网络是否限制非常用端口。

不要为了排障永久将 8000 开放给整个公网。

## 13. 第三层验收：Campaign Optimizer `final_plan`

平台冒烟通过后，再验证真实业务入口：

```text
POST /api/v1/plan-reviews
```

先使用仓库自带 fixture。在 ECS 的 API 目录执行：

```bash
curl --fail-with-body \
  -X POST 'http://127.0.0.1:8000/api/v1/plan-reviews' \
  -H 'X-API-Key: <SERVICE_API_KEY>' \
  -H 'Idempotency-Key: plan-demo-001' \
  -H 'Content-Type: application/json' \
  --data-binary '@../docs/campaign-optimizer-llm-integration-main/tests/fixtures/plan_a/final_plan.demo.json'
```

验收以下内容：

- HTTP 状态码为 `201`；
- 响应 `source` 为 `ONTOLOGY_ENGINE`；
- `plan_id` 与请求一致；
- 返回 canonical `ontology_version`；
- 响应头中存在 `X-Ontology-Checksum`；
- 响应符合 `ontology_review` schema；
- 自带 `plan_a` fixture 的整体 verdict 应为 `CONFLICT`；
- R5 对应结果应为 `CONFLICT`，并关联预期的 review fact。

保存响应和响应头：

```bash
curl --silent --show-error \
  -D plan-review.headers \
  -o plan-review.response.json \
  -X POST 'http://127.0.0.1:8000/api/v1/plan-reviews' \
  -H 'X-API-Key: <SERVICE_API_KEY>' \
  -H 'Idempotency-Key: plan-demo-evidence-001' \
  -H 'Content-Type: application/json' \
  --data-binary '@../docs/campaign-optimizer-llm-integration-main/tests/fixtures/plan_a/final_plan.demo.json'
```

然后使用 Campaign Optimizer 实际生成的完整 `final_plan` 替换 fixture，再调用一次：

```bash
curl --fail-with-body \
  -X POST 'http://127.0.0.1:8000/api/v1/plan-reviews' \
  -H 'X-API-Key: <SERVICE_API_KEY>' \
  -H 'Idempotency-Key: <本次业务请求唯一Key>' \
  -H 'Content-Type: application/json' \
  --data-binary '@<实际-final_plan.json>'
```

每一次新的业务请求都要使用新的 `Idempotency-Key`。同一个请求的重试必须继续使用原 Key。

Plan Review 可能返回的业务 verdict 包括：

- `SUPPORT`；
- `CONFLICT`；
- `NOT_APPLICABLE`；
- `INSUFFICIENT_EVIDENCE`；
- `UNVERIFIED`。

`INSUFFICIENT_EVIDENCE` 或 `UNVERIFIED` 不一定代表系统故障；需要结合返回的缺失证据或规则覆盖情况判断。

## 14. 第四层验收：幂等和持久化

先运行普通冒烟测试，它会保存一个 Review ID：

```bash
SMOKE_API_KEY='<SERVICE_API_KEY>' ./scripts/smoke.sh
```

重启容器：

```bash
sudo docker compose restart
sudo docker compose ps
```

等待服务恢复后执行：

```bash
SMOKE_API_KEY='<SERVICE_API_KEY>' ./scripts/smoke.sh --verify-persistence
```

通过标准：

- 重启前创建的 Review 仍然可以读取；
- 使用相同 principal、相同 `Idempotency-Key` 和相同 payload 重放时，返回相同 `review_id`；
- 没有产生重复 Review；
- `/ready` 在重启后重新返回 200。

如果相同 Key 配合不同 payload，API 应返回：

```text
409 IDEMPOTENCY_CONFLICT
```

这属于正确的保护行为。

## 15. 常见错误判断

| 现象/错误 | 含义 | 处理方向 |
| --- | --- | --- |
| RAM 控制台无 ECS 权限 | 主账号未正确授权 | 联系主账号管理员检查策略 |
| `/health` 200，`/ready` 503 | 进程活着，但依赖未就绪 | 查看 ready 响应和容器日志 |
| `AUTH_REQUIRED` / `INVALID_API_KEY` | API Key 缺失或错误 | 检查请求头和 `.env` 映射 |
| `IDEMPOTENCY_KEY_REQUIRED` | 写请求缺少幂等 Key | 添加 `Idempotency-Key` |
| `IDEMPOTENCY_CONFLICT` | 相同 Key 被用于不同 payload | 新业务请求使用新 Key |
| `UNKNOWN_RULE` | 请求包含未知规则 ID | 检查 canonical ontology 规则列表 |
| `UNKNOWN_CONCEPT` | 输入引用未知 concept | 按 ontology 概念定义修正 |
| `MISSING_REQUIRED_METRIC` | 规则需要的指标或 baseline 不完整 | 补齐请求证据 |
| `ONTOLOGY_VERSION_MISMATCH` | 请求期望版本与服务加载版本不同 | 核对版本及 checksum |
| `FINAL_PLAN_SCHEMA_INVALID` | `final_plan` 不符合共享 schema | 先修复 Optimizer 输出 |
| 远程连接 8000 超时 | 安全组、IP 或端口映射问题 | 对照第 12 节排查 |
| 容器重启后数据丢失 | `/data` 未使用持久卷或配置改变 | 检查 Compose volume |

所有错误响应都会带 `X-Request-ID`。提交问题时记录 Request ID，但不要附带 API Key 或 `.env`。

## 16. 验收记录模板

部署负责人完成后复制以下模板填写：

```text
Ontology Review API ECS 验收记录

执行人：
执行时间：
ECS 地域：
ECS 实例 ID：
操作系统：
镜像版本：ontology-review-api:0.1.0
API 地址：
Ontology 版本：
Ontology checksum：

[ ] RAM 权限正常
[ ] ECS 和安全组配置完成
[ ] Docker/Compose 可用
[ ] 容器启动成功
[ ] GET /health = 200
[ ] GET /ready = 200
[ ] MATCH 通过，Review ID：
[ ] CONFLICT 通过，Review ID：
[ ] NO_COVERAGE 通过，Review ID：
[ ] plan_a final_plan 返回预期 CONFLICT
[ ] 真实 final_plan 联调完成
[ ] 相同请求幂等重放返回相同 Review ID
[ ] 容器重启后 Review 仍可读取
[ ] ECS 端口仅对获准来源开放
[ ] API Key/.env 未提交或泄露

失败项：
Request ID：
相关日志：
下一步负责人：
```

## 17. 验收通过标准

以下条件全部满足才算完成：

1. 自动化测试没有非预期失败；
2. ECS 容器稳定运行；
3. `/health` 和 `/ready` 均返回 200；
4. Review 冒烟测试覆盖并通过 `MATCH`、`CONFLICT`、`NO_COVERAGE`；
5. Campaign Optimizer fixture 成功通过 `/api/v1/plan-reviews`；
6. 实际 `final_plan` 完成至少一次联调；
7. 幂等重放行为正确；
8. 容器重启后数据仍存在；
9. API Key、`.env` 和 SQLite 文件没有公网暴露；
10. 验收记录已提交给团队。

出现以下任一情况应阻止验收：

- `/ready` 不是 200；
- 三种基础 Review 结果任一失败；
- `final_plan` 或返回结果不符合共享 schema；
- ontology 路径、版本或 checksum 错误；
- 重启后数据或幂等记录丢失；
- SSH、API 或数据库端口对非授权网络开放。

## 18. 验证结束后的资源处理

如果 ECS 只是临时验证环境：

1. 保存验收记录和必要日志；
2. 备份需要保留的数据；
3. 确认不再需要服务器；
4. 在 ECS 控制台释放实例。

按量付费实例仅关机仍可能产生系统盘、公网 IP 等费用。不再使用时应由负责人确认后释放。释放会删除实例及相关数据，操作前必须完成备份。

---

## 附录：本项目对应的关键文件

- `README.md`：接口契约、运行和 ECS 部署说明；
- `.env.example`：运行时环境变量模板；
- `docker-compose.yml`：API、端口、持久卷和只读挂载配置；
- `Dockerfile`：镜像构建和运行用户；
- `scripts/smoke.sh`：基础 Review 和重启持久化测试；
- `tests/test_api.py`：基础 API、鉴权、幂等和故障行为；
- `tests/test_plan_review_api.py`：Campaign Optimizer `final_plan` 联调契约；
- `tests/test_postgres_integration.py`：后续 PostgreSQL/PolarDB 集成测试。
