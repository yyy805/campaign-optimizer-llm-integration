# Ontology Review API — Build Plan

## 1. 最终目标

搭建一个可以部署到阿里云 ECS 的 Ontology Review API，让 MTA、Agent 和前端能够通过 HTTP 地址提交分析结果，并得到可追踪的本体 Review 结果。

完整链路：

```text
本体规则文件
   ↓
API 读取规则
   ↓
接收 MTA / Agent 分析结果
   ↓
判断 Review 类型
   ↓
MATCH / CONFLICT / NO_COVERAGE
   ↓
保存 Review 和用户反馈
   ↓
提供给 Agent 和前端调用
   ↓
Docker 打包
   ↓
部署到阿里云 ECS
```

## 2. 建设原则：同一套正式 API 持续完善

我们不会先做一套用完就丢的临时 API。所有阶段都在同一个项目、同一套数据模型和同一组接口上继续建设，前一阶段的成果直接成为下一阶段的基础，最终部署为 Demo 正式使用的服务。

第一个可运行里程碑先打通核心链路：

- 读取 Ontology 当前版本和 R1–R7 规则；
- 接收 MTA 或 Agent 提交的分析结果；
- 返回 `MATCH`、`CONFLICT` 或 `NO_COVERAGE`；
- 保存每次 Review 及判断依据；
- 接收并保存用户反馈；
- 提供浏览器可操作的 API 测试页面；
- 使用 Docker 打包；
- 可以部署到 ECS，供其他模块通过 HTTP 调用。

随后在这套服务上继续补齐 Demo 所需能力。每完成一个阶段，都必须继续使用同一个 Docker 服务启动，通过健康检查和阶段测试，并且能保留已有数据升级到下一阶段：

- Review 列表、筛选和状态流转；
- 反馈证据、处理意见和历史记录；
- 人工审批与 Ontology 变更任务；
- Agent、前端和本地知识库联调；
- Docker、ECS、共享访问地址和部署验收；
- Demo 需要的基础访问控制、日志和错误处理。

以下是产品边界，不是暂缓建设的功能：

- 用户反馈不能自动修改 Ontology；
- Review API 不直接篡改 MTA 原始结果；
- Review API 不直接执行预算调整；
- 本地知识库是独立组件，通过接口与 Review API 协作，不重复建设在 Review API 内；
- 正式的 Ontology 修改必须产生新版本，并记录原因、证据、审批人和回滚点。

## 3. Review API 必须负责的完整范围

这一节是 API 团队的正式工作边界。下面列出的能力需要进入同一个 Review API 项目，并在 Demo 验收时能够运行和验证。

### 3.1 API 契约与基础规范

- 使用版本化路径，例如 `/api/v1`；
- 定义统一的请求、响应和错误格式；
- 定义 Review、反馈、治理任务的状态枚举；
- 对必填字段、类型、取值范围和字符串长度进行校验；
- 为写接口支持幂等键，避免 Agent 重试时重复创建记录；
- 为列表接口支持分页、排序和筛选；
- 为所有请求生成 `request_id`，并在响应和日志中返回；
- 维护自动生成的 OpenAPI 文档和 `/docs` 测试页面；
- 对不兼容的接口变更使用新的 API 版本，不能直接破坏旧调用方。

Demo 中所有写接口统一使用 `Idempotency-Key` 请求头。幂等记录按“调用方身份 + 接口 + Key”隔离并至少保留 24 小时；相同 Key 和相同请求返回原响应，相同 Key 但请求内容不同返回 `409 IDEMPOTENCY_CONFLICT`。

### 3.2 Ontology 规则加载与版本管理

- 将 `ontology 概念卡` 转换或整理为 API 可稳定读取的规则包；
- 加载 R1–R7、G1–G2、字段定义、规则状态和优先级；
- 启动时校验规则包格式、完整性、版本号和校验值；
- 规则校验失败时拒绝启动，不能静默使用残缺规则；
- 提供当前规则版本和加载状态查询接口；
- 每条 Review 固化实际执行的 Ontology 版本；
- 在 Review 记录中保存规则包和镜像的制品元数据；ECS / 发布负责人在镜像仓库保留当前版及上一版制品，用于定位和回滚；
- 新规则发布前运行回归测试，确认旧能力未被意外破坏。

Demo 不做运行中热更新：规则包只在进程启动时一次性加载并锁定。新规则通过新镜像或新挂载版本重启发布，因此同一请求执行期间版本不会变化。调用方可提交 `expected_ontology_version`；与实际版本不一致时返回 `409 ONTOLOGY_VERSION_MISMATCH`。

### 3.3 Review 创建与判断

- 接收 MTA 或 Agent 提交的客户、指标、候选触发规则、建议动作和上下文；
- 保存原始请求，避免后续无法还原当时输入；
- 根据有效 Ontology 规则判断 `MATCH`、`CONFLICT` 或 `NO_COVERAGE`；
- 处理多条规则同时命中、规则优先级和规则停用情况；
- 对未知规则、缺失指标和不合法输入返回明确错误；
- 返回 Review ID、判断结果、理由、命中规则、规则版本和证据引用；
- 对相同幂等键返回原有结果，而不是创建重复 Review；
- 判断逻辑必须由规则驱动，不能只把三个演示案例写死在代码里。

`triggered_rules` 只代表 MTA / Agent 声称触发的候选规则，不能直接决定 Review 结果。Review API 必须根据指标独立计算有效命中规则，再比较候选规则与实际命中结果并生成判断。

| 输入情况 | API 处理 |
|---|---|
| 已知且有效规则，指标满足 | 参与判断并记录命中 |
| 已知但已停用规则，且没有其他有效命中 | 返回 `NO_COVERAGE` |
| 指标没有命中任何有效规则 | 返回 `NO_COVERAGE` |
| 请求包含未知规则 ID | 返回 `422 UNKNOWN_RULE` |
| 缺少判断所必需的指标 | 返回 `422 MISSING_REQUIRED_METRIC` |
| 有效规则产生互斥结论 | 返回 `CONFLICT` |
| 混合未知规则与有效规则 | 整个请求返回 `422`，不忽略坏数据 |

### 3.4 Review 查询与状态管理

- 根据 Review ID 查询完整详情；
- 分页查询 Review 列表；
- 支持按客户、结果、状态、规则、Ontology 版本和时间筛选；
- 定义合法状态转换，例如待 Review、已反馈、治理处理中和已关闭；
- 拒绝非法状态跳转；
- 使用记录版本号或乐观锁，避免多人更新时相互覆盖；
- 保留状态变化历史、操作者和时间；
- 不允许调用方直接覆盖原始分析和原始判断记录。

Review 状态固定为：

```text
PENDING_USER_REVIEW → FEEDBACK_RECEIVED → GOVERNANCE_IN_PROGRESS → CLOSED
                    └──────────────────────────────────────────→ CLOSED
```

- 提交有效反馈时由 API 自动进入 `FEEDBACK_RECEIVED`；
- 创建治理任务时自动进入 `GOVERNANCE_IN_PROGRESS`；
- 无需治理的 Review 可由 Reviewer 关闭；
- 治理任务完成后由 API 自动关闭关联 Review；
- `CLOSED` 为终态，重新处理必须创建新 Review 或明确的重开记录；
- 所有状态写入必须携带当前 `record_version`，版本落后返回 `409 STALE_RECORD_VERSION`。

### 3.5 用户反馈

- 接收同意、不同意、请求人工检查、选择解释、报告缺口和补充证据等反馈；
- 根据 `MATCH`、`CONFLICT`、`NO_COVERAGE` 限制可用反馈动作；
- `NO_COVERAGE` 不提供直接批准操作；
- 保存反馈人、反馈时间、动作、说明、证据和处理状态；
- 支持查询某条 Review 的全部反馈历史；
- 防止网络重试产生重复反馈，并防止并发更新相互覆盖；
- 反馈不能自动修改 Ontology、MTA 原始结果或广告预算；
- 需要治理时，根据明确条件生成治理任务。

反馈状态仅由 API 根据后续流程派生：`RECORDED`、`LINKED_TO_GOVERNANCE`、`RESOLVED`，不提供任意修改反馈状态的接口。同一用户可以追加新的反馈；只有相同身份、相同 `Idempotency-Key` 的网络重试才视为重复。修正旧反馈时追加新记录并引用 `supersedes_feedback_id`，不覆盖历史。

### 3.6 Ontology 治理任务

- 从需要处理的反馈生成治理任务；
- 保存关联 Review、问题类型、建议、证据、创建人和创建时间；
- 支持任务列表、详情、筛选和历史查询；
- 支持领取、审批、驳回、完成和取消等合法动作；
- 记录每次动作的操作者、意见、时间和前后状态；
- 需要修改 Ontology 时，要求记录修改原因、证据、审批人、目标版本和回滚点；
- 任务批准不等于 Ontology 已经写回，必须分别记录审批完成与发布完成；
- API 不绕过人工审批直接修改正式 Ontology。

治理任务状态固定为：

```text
OPEN → CLAIMED → APPROVED → PUBLISHED → COMPLETED
               └→ REJECTED
OPEN / CLAIMED ─→ CANCELLED
```

`REJECTED`、`CANCELLED`、`COMPLETED` 为终态。Ontology 发布由团队的规则发布人或 CI 发布流程执行，不由 Review API 修改文件；发布完成后，发布方调用回执接口提交新版本、规则包校验值、制品地址和回滚版本，API 才记录 `PUBLISHED`。审批人不能审批自己创建的任务。

### 3.7 证据与本地知识库对接

- 定义稳定的证据引用格式，包括 `evidence_id`、文档、位置、知识库版本和摘要；
- 调用本地知识库查询或验证证据；
- 配置知识库地址、鉴权和超时；
- 知识库不可用时返回明确的降级状态，不能伪造证据；
- 保存必要的证据快照，保证知识库内容更新后仍能理解历史 Review；
- 不在 Review API 中重复实现知识库索引和检索算法；
- 日志中不得泄漏知识库密钥或敏感文档内容。

Review 创建不因知识库短暂不可用而整体失败。API 先完成规则判断，并记录证据状态：`AVAILABLE`、`PENDING` 或 `DEGRADED`；依赖失败时返回成功的 Review 和 `DEGRADED` 标记，同时记录可重试错误。证据可以通过专用接口查询或刷新。

证据快照只保存来源、定位信息、知识库版本、脱敏摘要和校验值，不保存整篇文档。摘要限制长度，敏感字段写入前脱敏；访问证据需要 Reviewer 权限。原始请求和证据快照默认保留 30 天，审计元数据保留至 Demo 结束；管理员可以导出或删除，实际期限通过环境配置调整。

### 3.8 Agent 对接

- 为 Agent 提供创建和查询 Review 的 HTTP 接口；
- 定义鉴权、超时、重试、幂等和错误码规则；
- 保证 Agent 重试不会创建重复记录；
- 对短暂依赖故障返回可重试错误，对业务错误返回不可重试错误；
- 返回足够的判断理由、规则和证据，供 Agent 生成用户可理解的回答；
- 跑通至少一条来自真实 Agent 链路的请求，而不只是手工调用 `/docs`。

Demo API Key 必须映射到明确的 `principal_id` 和角色，不能由调用方自由填写操作者。角色至少包括：`SERVICE`（Agent调用）、`REVIEWER`（查看与反馈）、`GOVERNANCE_APPROVER`（审批）、`PUBLISHER`（登记发布）和 `ADMIN`（配置、导出及删除）；API 根据角色限制操作并记录可信操作者。

### 3.9 前端对接

- 提供 Review 列表、详情、反馈和治理状态接口；
- 支持前端需要的分页、排序、筛选和状态显示；
- 配置明确的 CORS 允许来源；
- 返回统一、可展示的验证错误和业务错误；
- 不把数据库内部字段直接暴露为不稳定的前端契约；
- 跑通用户查看 Review、查看证据、提交反馈、看到处理状态的真实流程。

### 3.10 数据库、迁移与数据保护

- 保存 Review、反馈、证据引用、治理任务、状态历史和幂等记录；
- Demo 单实例使用 SQLite，并通过 Docker 持久卷保存；
- 使用 schema migration 管理数据库结构变化；
- 部署升级前备份数据库，并验证恢复方法；
- 容器和 ECS 重启后数据不得丢失；
- 对关联数据使用事务，避免只保存一半；
- 记录创建时间、更新时间和记录版本；
- 为未来切换 PostgreSQL 保持清晰的数据访问边界；
- 明确 Demo 的单实例限制，不能把 SQLite 当作多实例共享数据库。

API 团队负责提供 migration、备份和恢复脚本；ECS 负责人负责配置备份存储位置、凭据和定时执行。部署脚本在 migration 前触发备份，备份失败则停止升级；发布验收必须实际完成一次恢复演练。

### 3.11 安全、日志和运行状态

- 除公开健康检查外，业务接口使用最小 API 鉴权；
- 密钥通过环境变量或 ECS 密钥配置注入，不写进代码和镜像；
- 限制请求大小、字段长度和允许的访问来源；
- 生产式部署时控制 `/docs` 是否对外开放；
- 提供存活检查和就绪检查；
- 使用结构化日志记录 `request_id`、`review_id`、Ontology 版本、耗时和错误类型；
- 不在日志中记录 API Key、完整敏感输入或个人信息；
- 对规则加载失败、数据库失败、知识库失败和内部异常返回一致错误；
- 为 ECS 容器配置自动重启和日志保留策略。

### 3.12 测试、Docker、ECS 与交付

- 为规则判断、状态转换和输入校验编写单元测试；
- 为 API、数据库和知识库适配器编写集成测试；
- 建立覆盖核心规则和异常情况的 Golden Tests；
- 跑通 Agent、Review API、本地知识库、前端和反馈治理的端到端测试；
- 提供 `Dockerfile`、`docker-compose.yml`、依赖锁定和环境变量模板；
- 使用同一 Docker 构建方式贯穿本地开发、测试和 ECS 部署；
- 固定镜像版本，禁止只使用不可追踪的 `latest`；
- 提供 ECS 部署、配置、数据挂载、升级、回滚和 smoke test 说明；
- 验证从旧镜像升级后历史数据仍可读取，并能回滚；
- 交付 API 文档、示例请求、测试结果和 Demo 演示步骤。

### 3.13 API 团队需要产出的接口

最终接口以版本化路径发布，至少包括：

```text
GET  /health
GET  /ready
GET  /api/v1/ontology/version

POST /api/v1/reviews
GET  /api/v1/reviews
GET  /api/v1/reviews/{review_id}
POST /api/v1/reviews/{review_id}/close
GET  /api/v1/reviews/{review_id}/evidence
POST /api/v1/reviews/{review_id}/evidence/refresh

POST /api/v1/reviews/{review_id}/feedback
GET  /api/v1/reviews/{review_id}/feedback

GET  /api/v1/governance-tasks
GET  /api/v1/governance-tasks/{task_id}
POST /api/v1/governance-tasks/{task_id}/claim
POST /api/v1/governance-tasks/{task_id}/approve
POST /api/v1/governance-tasks/{task_id}/reject
POST /api/v1/governance-tasks/{task_id}/cancel
POST /api/v1/governance-tasks/{task_id}/publication
POST /api/v1/governance-tasks/{task_id}/complete
GET  /api/v1/governance-tasks/{task_id}/history

GET  /api/v1/admin/export
DELETE /api/v1/admin/reviews/{review_id}
```

接口名字在正式编码时可以小幅调整，但任何调整都必须保持上述业务能力完整，并同步更新 Agent、前端和 API 文档。

### 3.14 API 不负责、但必须配合对接的内容

- 不负责开发 MTA 算法，但负责接收并校验 MTA 输出；
- 不负责 Agent 对话编排，但负责提供稳定的 Agent 调用接口；
- 不负责前端页面设计和实现，但负责提供前端所需数据和错误信息；
- 不负责本地知识库的索引算法，但负责查询证据并处理不可用情况；
- 不负责自动执行广告预算调整；
- 不负责未经审批自动修改 Ontology；
- 不负责重新搭建百炼知识库。

## 4. 搭建步骤

### Step 1：定义 API 输入和输出

需要确定 MTA / Agent 提交哪些字段，以及 Review API 返回哪些字段。

输入示例：

```json
{
  "client_id": "demo-client",
  "triggered_rules": ["R3"],
  "metrics": {
    "mta_roas": 1.6,
    "baseline_roas": 1.0
  },
  "proposed_action": "预算增加10%"
}
```

输出示例：

```json
{
  "review_id": "review-001",
  "ontology_version": "v1.1-demo",
  "outcome": "MATCH",
  "reason": "分析结果符合 R3",
  "matched_rules": ["R3"],
  "evidence_refs": [],
  "evidence_status": "PENDING",
  "status": "PENDING_USER_REVIEW",
  "record_version": 1
}
```

为什么需要：各模块必须使用同一种数据格式，否则前端、Agent 和 API 无法稳定对接。

完成标准：输入、输出字段有明确说明，并能够被自动校验。

同时从一开始固定以下兼容规则：

- API 使用版本路径，例如 `/api/v1/reviews`；
- 已提供给 Agent 和前端的字段不随意删除或改名；
- 写操作携带幂等键，重复提交不会生成两条 Review；
- 统一错误码、状态枚举、分页、排序和筛选格式；
- 数据表结构变化必须通过 migration 升级，并保留回滚办法。

### Step 2：读取本体版本和规则

API 使用一个经过校验并带版本号的 Ontology 规则包，而不是在 ECS 上临时读取散落文件。规则包来源于 `ontology 概念卡`，包含：

- 实际发布的 Ontology 版本（当前 Demo 基线为 `v1.1-demo`）；
- R1–R7 规则；
- G1–G2 治理规则；
- 规则状态和优先级；
- 相关字段定义。

为什么需要：Review 结果必须说明它依据哪个本体版本和哪些规则产生，不能脱离本体自行判断。

规则包随 Docker 镜像发布，或通过只读目录挂载。启动时校验文件完整性和版本；校验失败则拒绝启动，不能带着未知规则运行。每次发布保留前一版规则包用于回滚。

完成标准：API 能返回实际加载的版本，识别 R1–R7（包括已经停用的 R7），并把本次执行的版本固化在每条 Review 中。

### Step 3：实现三类 Review 判断

固定输出以下三类结果：

- `MATCH`：MTA 结果与 Ontology 规则一致；
- `CONFLICT`：同时触发冲突规则，或结果不符合规则约束；
- `NO_COVERAGE`：当前 Ontology 没有有效规则能够解释该结果。

首批 Golden Test 准备三个演示案例，后续规则继续加入同一测试体系：

1. R3 场景返回 `MATCH`；
2. R1 和 R2 冲突场景返回 `CONFLICT`；
3. 已停用的 R7 场景返回 `NO_COVERAGE`。

为什么需要：三类结果是用户反馈、Agent 解释和本体治理流程共同使用的基础状态。

完成标准：三个固定案例能够重复得到预期结果，同时返回判断理由和规则依据。

### Step 4：建立可持续使用的数据模型并保存 Review

Demo 阶段先使用 SQLite，但从第一天就使用持久卷和 schema migration，避免部署后重新推倒。保存内容包括：

- 唯一 `review_id`；
- 原始输入；
- Review 结果；
- 命中的规则；
- Ontology 版本；
- 判断理由；
- 创建时间；
- 当前处理状态。

为什么需要：如果不保存，调用结束后结果就会消失，无法回查用户对哪一次分析进行了反馈。

SQLite 只支持一个 Review API 实例；如果后续需要多实例或更高并发，则沿同一数据访问层切换到 PostgreSQL。每次部署前备份数据库，升级失败可以回滚镜像和数据。

完成标准：创建 Review 后能通过 ID 再次查询；容器重启或升级后记录仍然存在；migration 和备份恢复经过测试。

### Step 5：实现完整的用户反馈与治理入口

用户可以针对一条 Review 提交反馈，但反馈只被记录，不直接改动本体或 MTA。

不同结果允许的行为：

- `MATCH`：同意、不同意或补充证据；
- `CONFLICT`：选择更合理的解释、请求人工检查或补充证据；
- `NO_COVERAGE`：报告规则缺口或提交新情况，不提供直接批准操作。

必须遵守：

```text
用户同意 ≠ 已批准修改
批准修改 ≠ 已完成写回
用户反馈 ≠ 自动修改 Ontology 或 MTA
```

反馈记录至少包含反馈动作、说明、证据引用、提交人、时间和处理状态。需要修改 Ontology 时，API 生成待审批的变更任务，不直接改正式规则。

为什么需要：既保留用户判断和证据，也守住“反馈不等于规则已经修改”的治理边界。

完成标准：反馈能够保存和回查；能够形成待处理治理任务；提交反馈后 Ontology 文件保持不变。

### Step 6：提供 Demo 真正使用的接口

Demo 目标接口：

```text
GET  /health
GET  /ready
GET  /api/v1/ontology/version
POST /api/v1/reviews
GET  /api/v1/reviews/{review_id}
GET  /api/v1/reviews
POST /api/v1/reviews/{review_id}/close
GET  /api/v1/reviews/{review_id}/evidence
POST /api/v1/reviews/{review_id}/evidence/refresh
POST /api/v1/reviews/{review_id}/feedback
GET  /api/v1/reviews/{review_id}/feedback
GET  /api/v1/governance-tasks
GET  /api/v1/governance-tasks/{task_id}
POST /api/v1/governance-tasks/{task_id}/claim
POST /api/v1/governance-tasks/{task_id}/approve
POST /api/v1/governance-tasks/{task_id}/reject
POST /api/v1/governance-tasks/{task_id}/cancel
POST /api/v1/governance-tasks/{task_id}/publication
POST /api/v1/governance-tasks/{task_id}/complete
GET  /api/v1/governance-tasks/{task_id}/history
GET  /api/v1/admin/export
DELETE /api/v1/admin/reviews/{review_id}
```

同时提供自动生成的测试页面：

```text
http://服务器地址:8000/docs
```

为什么需要：在前端尚未完成时，组员仍然可以通过网页提交请求并验证 API。

Review、反馈和治理任务都要有明确状态机；非法状态转换返回错误。更新请求携带记录版本号，避免两个人同时处理时互相覆盖。

完成标准：所有接口都能在 `/docs` 页面直接操作；Agent 能可靠提交 Review；前端能分页查询、展示结果并提交反馈；治理人员能领取、批准、驳回、完成并查询历史。

### Step 7：建立 Golden Tests

至少验证：

1. R3 输入返回 `MATCH`；
2. R1 和 R2 同时出现返回 `CONFLICT`；
3. R7 输入返回 `NO_COVERAGE`；
4. Review 可以保存并再次查询；
5. 用户反馈可以保存；
6. 用户反馈不会自动修改 Ontology；
7. 每条 Review 都记录 Ontology 版本。
8. 无效输入、未知规则和损坏规则包会被明确拒绝；
9. 重复请求不会重复创建 Review；
10. 并发反馈不会互相覆盖；
11. 本地知识库不可用时返回可识别的降级结果；
12. 数据库升级后旧数据和旧客户端仍可使用。

为什么需要：以后修改规则、Agent 或模型时，可以快速发现原有行为是否被意外破坏。

完成标准：所有测试可以通过一条命令运行并全部通过。

### Step 8：从第一阶段开始持续使用 Docker

需要提供：

- `Dockerfile`；
- `docker-compose.yml`；
- 环境变量模板；
- SQLite 持久化目录；
- 固定镜像版本；
- 规则包只读挂载或随镜像发布；
- 启动和检查说明。

为什么需要：把代码、Python 环境和依赖固定在同一个运行包中，保证本地测试版本与 ECS 版本一致。

完成标准：组员能够用 Docker 启动服务；每个建设阶段使用同一镜像构建方式；关闭、升级再启动后 Review 数据仍然存在。

### Step 9：部署到阿里云 ECS

ECS 侧需要：

- 安装 Docker；
- 上传或拉取 API 项目；
- 启动容器；
- 配置持久化目录；
- 配置最小 API 鉴权、密钥和允许访问的前端来源；
- 按需开放 API 端口，决定是否对外暴露 `/docs`；
- 配置容器自动重启、固定镜像版本和结构化日志；
- 提供团队可访问的地址。

部署后的调用关系：

```text
MTA / Agent → Review API → 获得判断结果
前端 → Review API → 展示 Review
用户 → 提交反馈 → Review API 保存治理证据
```

为什么需要：部署后 API 才不依赖某位组员的个人电脑，Agent、前端和其他成员可以共同访问。

部署必须可重复执行，并包括升级、回滚及部署后 smoke test。日志需要记录请求 ID、Review ID、Ontology 版本和错误原因，但不能记录密钥。

完成标准：获得授权的另一台电脑能访问服务并完整跑通三个演示案例；可以从上一镜像升级并保留数据；出现问题时能回滚。

### Step 10：完成真实模块联调

Agent 调用契约必须包含鉴权、超时、重试、幂等键和错误处理。前端契约必须包含 CORS、分页、排序、筛选、状态枚举和统一错误响应。

本地知识库通过独立接口提供证据，至少约定：

- `evidence_id`、来源文档、片段位置、知识库版本和摘要；
- 查询鉴权、超时和不可用时的降级行为；
- Review 只保存稳定的证据引用和必要快照，不复制整个知识库。

为什么需要：只有真实 Agent 能提交、真实前端能 Review、真实知识库能提供证据，API 才是 Demo 的组成部分，而不只是独立测试程序。

完成标准：跑通一次真实端到端流程，并能通过 Review ID 在日志、数据库、前端和治理记录中追踪全过程。

## 5. 逐步 Build 成最终 Demo 的实施路径

```text
阶段一：建立可部署的正式工程底座并能判断
版本化接口 → 数据模型与 migration → 规则包 → 三类判断 → Docker → smoke test

阶段二：能追踪和治理，并持续可部署
保存 Review → 反馈与证据 → 治理状态机 → 历史记录 → Golden Tests → 镜像升级测试

阶段三：真正接入 Demo，并持续可部署
Agent 可靠调用 → 前端展示与反馈 → 本地知识库证据 → 端到端测试 → 镜像升级测试

阶段四：成为团队共享服务
ECS 发布 → 鉴权与日志 → 自动重启 → 升级与回滚 → 跨电脑端到端验收
```

这个顺序不是制作多个版本，而是在同一个正式工程中逐层增加能力。逻辑是：判断不正确就没有接入价值；判断正确后必须可追踪和治理；内部链路稳定后再接 Agent、前端和知识库；最后把完全相同的服务部署到公共环境。

## 6. Demo 最终验收标准

只有同时满足以下条件，Review API 的 Demo 成果才算完成：

- 服务可以正常启动；
- `/health` 返回正常状态；
- `/ready` 只有在规则包和数据库可用时才通过，并作为 ECS 发布门禁；
- `/api/v1/ontology/version` 返回正确版本；
- `/docs` 可以打开并操作；
- 三个演示案例返回正确结果；
- 每条 Review 都包含规则依据和 Ontology 版本；
- Review 和反馈可以保存、查询；
- Review 列表可以筛选并查看处理状态；
- 用户反馈可以附带证据，并形成可追踪的治理任务；
- 服务重启后数据仍存在；
- 反馈不会自动修改 Ontology 或 MTA；
- Agent 能提交一次真实 Review 请求；
- 前端能读取结果并提交一次真实用户反馈；
- Review 记录能够关联本地知识库提供的证据引用；
- 重复请求、超时、未知规则和依赖不可用都有确定行为；
- Docker 版本可以启动；
- ECS 上的地址可以被授权的另一台电脑访问；
- 能从前一阶段镜像升级到最终镜像并保留数据，也能回滚。

## 7. Demo 之后的生产化工作

下面这些不阻塞 Demo，但正式生产上线前需要继续补强：

- 完整角色权限与企业身份认证；
- 高并发与多实例运行；
- 生产级数据库、备份和灾难恢复；
- 完整监控、告警与审计平台；
- HTTPS、正式域名和安全合规加固。
