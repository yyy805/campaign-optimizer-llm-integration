# MTA 模拟数据→本体 Demo 字段协作契约

本文不改动 MTA 现有数据契约，只说明本体 Demo 当前能复用什么、哪些规则仍被粒度阻塞，以及希望模拟数据后续补充什么。机器可读契约见 `field_mapping.json`。

## 现在可直接复用

- `amazon_ads_report_sample.csv` 的 `reportDate` / `accountId` / `normalizedTouchpoint` 可作 ETL 日期、账户和五段触点键。五段键必须原样保留，不拆短、不丢失互动类型。
- `impressions` / `clicks` / `cost` / `purchases` / `sales` 可分别作为曝光、点击、花费、平台购买和平台收入的日级触点原料。
- Markov 输出的 `touchpoint + roas` 可直接映射 R3 `mta_roas`，但只是当前报告窗口的触点快照。
- 触点比较表的 `models_consistent` / `calculation_valid` / `data_support_sufficient` 可用于派生 R5 的一致性状态；需要先约定哪些 outcome 必须同时通过。`RELIABLE` 只代表计算、支持度和模型一致性，不代表因果有效。

## 本体 ETL 转换

| 目标 | 转换 | 分母为 0 | 当前状态 |
|---|---|---|---|
| `acos` | `sum(cost) / sum(sales)` | `null`，不触发 | 缺 campaign ID，阻塞 |
| `ctr` | `sum(clicks) / sum(impressions)` | `null`，不触发 | 缺 campaign ID，阻塞 |
| `impressions_growth` | 本 7 日对前 7 日 | 前期为 0/不完整则 `null` | 缺 campaign ID，阻塞 |
| `roas` | `sum(sales) / sum(cost)` | `null`，不触发 | 缺 campaign ID，阻塞 |
| `cvr` | `sum(purchases) / sum(clicks)` | `null`，不触发 | 缺 campaign ID，阻塞 |

不得用“全年触点 MTA ROAS”替代“14 天 campaign 平台 ROAS”。现有 Ads 主键是 `reportDate + normalizedTouchpoint`，没有 campaign ID，因此 R1/R2/R4/R6 只能继续使用 Demo 断言 fixture，不声称已由 MTA 数据支持。

## 必须补充（Demo 真实串联前）

1. 在 Amazon Ads 日级模拟数据增加稳定 `campaign_id`，同一 campaign 可对应多个五段触点，且每日可稳定聚合。这是 R1/R2/R4/R6 的必要键。
2. 为归因输出提供批次 manifest，或在相关输出中增加 `report_start_date` / `report_end_date`，以防全年快照被当成 14 天结果。

## 可选增强（需双方另行确认）

- 如 MTA 团队愿意支持 campaign/window 归因，可提供带 `campaign_id` 的 14 天归因批次；这不是对现有稳定输出改名的要求。
- 共同确认一致性派生所需 outcome 集合（`converted_users` / `purchase_count` / `revenue`）。

## MTA 职责之外

- R5 只使用 MTA 可提供的贡献份额，以及同一报告窗口透明派生的花费份额和归因差异。
- R7 所需的未来趋势属于预测模块；当前没有真实预测输出，因此退出活跃 Demo 并返回 `NO_COVERAGE`。
- 上述字段在责任团队接入前继续使用明确标注的 `DEMO_ONLY_MOCK`，不当作 MTA 生产输出。

## 当前卡点能否绕过

| 卡点 | 现在能否继续 | 临时方案 | 进入真实串联 Demo 前是否必须解决 | 硬推风险 |
|---|---|---|---|---|
| Ads 数据没有 `campaign_id` | 可以继续开发 | R1/R2/R4/R6 使用断言 fixture 的模拟 campaign | 必须 | 把触点当 campaign 会产生错误聚合和虚假规则触发 |
| 归因输出没有显式窗口 | 可以继续开发 | R3 只称“当前 MTA 报告快照” | 必须提供 batch manifest 或日期后才能做窗口串联 | 全年结果可能被误讲成 14 天结果 |
| R5 输入缺少同窗口分母 | 不触发规则 | 返回 `NO_COVERAGE`，不静默补值 | Demo 前必须 | 跨窗口比较会产生错误结论 |
| R7 没有预测输出 | 不执行规则 | 保持退役并返回 `NO_COVERAGE` | 接真实预测模块后重新评审 | 不得把年度快照描述成预测能力 |
| G1/G2 缺少完整动作参数 | 可以独立演示 | 使用合成 `new_bid` / `new_daily_budget` | 接自动执行前必须 | 只能证明护栏判断，不能证明真实动作链打通 |
| 只有一个账户数据 | 可以继续 | 由客户配置层模拟 A/B 风险偏好 | 多客户真实对比前再补 | 客户差异是治理配置效果，不是两套真实账户数据 |

因此当前推荐是继续推进字段适配、剧情数据生成器、规则匹配器和 Golden Test 骨架；但在 MTA 数据补齐前，必须把 campaign 规则标为 fixture-backed，把预测/提案标为 mock-backed。禁止用错误粒度强行拼接来换取“全链路已打通”的表面结果。

## 我们已经自行解决的部分

- `demo_data_adapter.json` 为当前 17 个五段触点建立了明确的 Demo Campaign 映射，覆盖 DSP、Sponsored Brands、Sponsored Display、Sponsored Products 四个模拟 Campaign。它只用于 Demo，不声称来自 Amazon/MTA 真实 Campaign。
- 同一适配文件为当前 MTA 全年结果补了 Ontology-owned batch manifest：`2026-01-01..2026-12-31`、UTC、完整报告窗口快照。
- R5 一致性暂按三个 outcome（converted_users、purchase_count、revenue）全部通过计算有效、数据支持和模型一致性后才为 consistent；缺任何 outcome 都返回 unknown 且不触发 R5。
- R5 不再依赖 Mock；R7 因缺少预测输出明确报告 NO_COVERAGE，不再静默给默认值。
- 除零、窗口不完整、未知字段和粒度不匹配的行为已经固定为 null/不触发、校验失败或阻止映射。

## 仍需别人确认的问题

### MTA 模拟数据组

1. **真实 Campaign 键是什么？** 我们的四个 Demo Campaign 只是临时分组。请确认后续是否在 Ads 模拟数据中提供稳定 `campaign_id`，以及一个 Campaign 能否对应多个五段 Touchpoint。
2. **每次归因运行的窗口从哪里读取？** 当前只有 summary 带起止日期。请确认由批次 manifest 统一提供，还是五份输出都带 `report_start_date/report_end_date`。
3. **是否愿意提供 14 天批次？** 如果不提供也可以，Ontology 会把 MTA 结果保持为 snapshot；但不能演示真实的 14 天 MTA 窗口变化。
4. **一致性必须覆盖哪些 outcome？** 我们当前临时采用三项全部通过。请确认是否允许某些场景只以 revenue outcome 作为 R5 依据。

### 优化/提案模块负责人

请确认 R5 所选 outcome、Touchpoint 主键、报告窗口和花费总分母。任一项缺失时 R5 返回 `NO_COVERAGE`。

### 预测模块负责人

若未来恢复 R7，请先确认预测输出粒度、预测窗口、模型版本、更新时间和证据含义；在此之前 R7 保持退役。

### 产品/本体负责人

请确认 R4 的“14 天 ROAS < 1”究竟指 14 天聚合值低于 1，还是连续 14 个自然日每天低于 1。当前断言和映射采用前者；两种语义会生成不同数据并触发不同结果。

## 可直接发给 MTA 模拟数据团队的消息

> 各位同学好，我们已按当前 AMC MTA 数据契约完成本体 Demo 字段映射。现有 `reportDate + normalizedTouchpoint` 日级 Ads 数据、Markov 触点 ROAS 和双模型一致性字段都可以直接复用，我们不需要修改它们现有的字段名或含义。
>
> 为了让 R1/R2/R4/R6 的 campaign 粒度 7/14 天规则能从模拟数据真实生成，请协助补充两项 Demo 必需信息：（1）在 Amazon Ads 日级模拟表中增加稳定 `campaign_id`；（2）为归因输出提供批次 manifest，或显式 `report_start_date` / `report_end_date`，避免把全年触点快照误用为 14 天 campaign 结果。
>
> 另有两项可选协作，需大家确认后再做：是否需要 14 天 campaign 归因批次；以及 R5 应采用哪个 outcome。R7 的预测输出默认不归 MTA 团队负责，除非后续另行达成责任协议。谢谢！
