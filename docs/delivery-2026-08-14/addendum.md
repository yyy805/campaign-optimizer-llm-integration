# PRD Addendum · 技术决策与机制（供架构/实施阶段）

> PRD 只写"要什么"，这里写"怎么做的"。回溯式记录，证据为 commit 与代码路径。

## A1 架构机制
- 三角色 runner 谱系：基座 `three_role_runner.py` + `v6–v13`（逐版继承；谱系自 v5 工作流契约演化）；Reviewer 走 Function Calling（`submit_reviewer_decision_v1`，tool_choice 强制，schema 动态收窄到 packet allowlist）。
- 可信上下文组装：`RequestBuilder` → release-pin 校验 → `LocalRuleRetriever` 按 ID 投影 → `ReviewerPacket`（digest = sha256(candidate_id+task+trusted+candidate+prompt_hash)）。
- 混合 RAG：权威=确定性投影；百炼知识库=补充检索（text-embedding-v4 + qwen3-rerank，阈值 0.60 为发布配置）。

## A2 安全机制
- fail-closed 链：schema → binding（digest/allowlist/动作语义）→ 预算 → 路由硬规则 → triage 弃权/白名单。
- 拒绝路径不回显伪造值（sentinel 测试覆盖）。
- 提示词/ schema 哈希钉死于 `agent_roles.vN.json`；加载器 mismatch 即 ValueError。

## A3 预算与成本
- `max_provider_calls_v12(rounds, triage) = 4*(rounds+1)+triage`；baseline=0 轮。
- 评测脚本 ledger：2×案例数；E2E：4（initial_render）/5（chat）。
- 全周期真实调用约 45 次、约 15 万 tokens（自报口径，无逐次台账）。

## A4 模型与端点
- 别名：triage=qwen3.6-flash，executor=qwen3.7-max，reviewer=qwen3.7-plus；OpenAI 兼容端点 cn-beijing（workspace maas 端点）。
- temperature=0、stream=False、enable_thinking=False（reviewer）。

## A5 数据与版本
- canonical 发布包 `.ontology_bundles/a83ff2b4…`（checksum 626cfbde…）；历史包 b90391ed… 供 plan_a demo。
- 冻结考卷 `tests/fixtures/llm_eval/reviewer_judgment_v1/`（8 案例）；路由卷 50；检索卷 12。
- 标签修订须带文档化 amendment（README Amendments 节）。

## A6 部署环境
- 部署方式：**ECS 直跑，不用 Docker**（2026-08-10 与老师确认）：git clone → uv sync --frozen（阿里云索引）→ systemd 跑 Streamlit headless 保活 → nginx 反代 → 安全组 8501；仓库自带 `.ontology_bundles` 与 `tests/fixtures`，release-pin 自校验可过。Dockerfile 保留为备选交付物（未经验证）。
- ECS：上海区域，跨区调北京百炼已确认可行；实例/公网 IP 等基础设施标识符存内部台账，不入库。
- 环境变量：DASHSCOPE_API_KEY / DASHSCOPE_WORKSPACE_ID 经 systemd EnvironmentFile 外置，不入库；可选 LLM_TIMEOUT_SECONDS。
- 中国网络：uv/pip 走阿里云镜像（UV_INDEX_URL / pip -i mirrors.aliyun）。

## A7 已知限制的技术根因
- Triage 误路由：flash 模型语义分类边界宽；硬路由 compound_or_extra 先拦解释动词前缀，非前缀问句才到 triage。
- 边界噪声：candidate_id/packet_digest 每轮新生成，模型在合规边界翻转 → 校准示例（v9）锚定。
