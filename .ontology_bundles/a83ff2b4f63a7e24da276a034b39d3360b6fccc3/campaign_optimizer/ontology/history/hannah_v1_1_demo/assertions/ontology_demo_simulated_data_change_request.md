# 本体知识库组模拟数据接口与验收需求

## 1. 目标

本体 Demo 需要用模拟数据完成：

```text
广告表现与用户路径
→ MTA Touchpoint 归因
→ 本体 Rule 判断
→ 诊断、建议与护栏
```

本文只规定数据交付结果、业务口径和验收标准，不限制模拟数据的内部生成方式，也不在本文划分人员职责。

### 1.1 核心原则

本体不要求模拟数据永久使用某一组固定字段名、文件名或存储格式。真正不可缺少的是：

```text
语义统一
+ 对象可识别
+ 数据可连接
+ 指标可计算
+ 版本可追溯
+ 结果可验证
```

因此：

- 本文出现的字段名均为当前实现示例，不是不可修改的命名标准；
- 字段可以改名，CSV 可以改为数据库，一个文件也可以拆成多个文件；
- 每次交付必须提供数据字典和“物理字段 → 本体逻辑字段”的版本化映射；
- 映射必须说明业务含义、数据类型、粒度、主键、单位、时间口径和空值语义；
- 字段仅改名时更新映射即可；业务含义、粒度或计算口径改变时必须升级接口版本；
- 不允许在字段名不变的情况下悄悄改变业务含义。

## 2. P0 核心接口

以下内容决定数据能否接入本体 Demo，必须首先满足。

### 2.1 必需数据类别

| 类别 | 必须表达的逻辑信息 | 当前字段示例 | 本体用途 |
|---|---|---|---|
| AMC 聚合路径 | 报告范围、账户、有序 Touchpoint Path、路径用户数、购买用户数、订单数、Outcome收入 | `report_start_date`、`advertiser_id`、`path`、`users`、`converted_users`、`purchase_count`、`revenue` | Markov/Shapley 输入 |
| Ads 日级表现 | 日期、账户、市场、币种、Touchpoint、曝光、点击、花费、平台购买、平台销售 | `reportDate`、`accountId`、`normalizedTouchpoint`、`impressions`、`clicks`、`cost`、`purchases`、`sales` | 经营指标与归因成本上下文 |
| MTA 归因输出 | 模型身份、Touchpoint贡献、归因收入、效率指标、模型一致性 | `attribution_model`、`touchpoint`、`attributed_revenue`、`roas`、`models_consistent` | R3、R5及归因解释 |
| 数据批次说明 | 接口版本、数据版本、报告窗口、时区、批次身份、场景清单、预期结果 | `schema_version`、`dataset_version`、`batch_id`、`scenario_expectations` | 可复现和验收 |

用户事件明细、Ground Truth 和地区增强数据属于分类增强要求，见后文。

### 2.2 Touchpoint 接口

Touchpoint 的逻辑身份必须表达以下维度，物理字段名可以不同：

```text
四段：AD_PRODUCT:FORMAT:PLACEMENT:CREATIVE
五段：AD_PRODUCT:FORMAT:PLACEMENT:CREATIVE:INTERACTION_TYPE
```

本体和当前 MTA 对接时使用五维 Touchpoint。数据可以直接提供拼接后的五维键，也可以分列提供四个广告维度和互动类型，但进入 MTA 前必须能确定性构造统一身份。

验收要求：

- 互动类型只能表达 `IMPRESSION` 或 `CLICK`；
- 结构性空值统一转换为 `UNSPECIFIED`；
- Path、Ads 表现和 MTA 输出中的同一 Touchpoint 必须得到完全相同的逻辑身份；
- 如果 Path 使用四维表达，Path 中的每一个有序元素必须同时绑定自己的互动类型；仅在表外提供一个无法对应到具体路径位置的互动类型不合格；
- `...:IMPRESSION` 与 `...:CLICK` 是两个不同的五段 Touchpoint；
- 不允许依靠模糊名称匹配或人工猜测完成 Join。

### 2.3 Grain 与 Join

每类数据必须声明粒度和可唯一识别记录的逻辑主键。下面的字段组合是当前示例，可以使用其他名称或等价结构：

| 数据 | 逻辑粒度 | 当前识别键示例 |
|---|---|---|
| AMC 聚合路径 | 报告窗口 + 账户 + 有序 Path | `report_start_date + report_end_date + advertiser_id + path` |
| Ads 日级表现 | 日期 + 账户 + 五维 Touchpoint | `reportDate + accountId + normalizedTouchpoint` |
| MTA 归因输出 | 模型 + 五维 Touchpoint | `attribution_model + touchpoint` |
| 模型比较 | 五维 Touchpoint + Outcome | `touchpoint + outcome` |
| Campaign 经营指标 | 日期/窗口 + Campaign 或 Ad Group | 稳定 `campaign_id` 或 `ad_group_id` |

同一批次中，账户、市场、币种、适用报告窗口和 Touchpoint 语义必须能确定性对齐。不同物理文件可以覆盖不同范围，但参与同一次计算的切片必须明确且一致。

## 3. 分类数据要求

### 3.1 Journey 与 Outcome

如交付用户事件明细，需要表达以下逻辑信息：

| 逻辑信息 | 当前字段示例 |
|---|---|
| 用户身份 | `synthetic_user_id` |
| Journey身份 | `journey_instance_id` |
| 事件身份 | `event_id` |
| Touchpoint或Outcome事件类型 | `event_type` |
| 可排序且含时区的事件时间 | `event_time` |
| Touchpoint身份维度 | `ad_product`、`format`、`placement`、`creative`、`interaction_type` |
| 是否购买 | `converted` |
| 订单数 | `purchase_count` |
| Outcome收入 | `revenue` |

语义要求：

- 路径按逻辑 Journey 身份分组，不按 User 直接合并；
- 同一个 User 可以拥有多个 Journey；
- 每个 Journey 有且仅有一个 `OUTCOME`；
- `converted=1` 时 `purchase_count>=1` 且 `revenue>0`；
- `converted=0` 时 `purchase_count=0` 且 `revenue=0`；
- 是否购买以 Outcome 行为准。

### 3.2 十四天 Journey 口径

仅对用户事件到聚合路径的构造使用以下规则：

- 只检查相邻 Touchpoint 的时间差；
- 间隔 `<=14天` 时保留；
- 间隔 `>14天` 时截掉该间隔左侧 Touchpoint 及更早部分；
- 最后一个 Touchpoint 到 Outcome 的时间差不参与14天判断。

如果直接交付已经聚合的 AMC Path，只需在批次说明中标明路径被视为有效路径；不要求从聚合表反推用户级时间。

用于验证 Journey 构造时，应提供：

| 案例 | 预期 |
|---|---|
| 相邻 Touchpoint 间隔 13 天 | 保留 |
| 相邻 Touchpoint 间隔 14 天 | 保留 |
| 相邻 Touchpoint 间隔 15 天 | 截断较早部分 |
| 总路径超过14天，但每段均不超过14天 | 完整保留 |
| 最后 Touchpoint 到 Outcome 超过14天 | 不因此截断 |

### 3.3 Ads 表现与经营指标

最低需要表达曝光数、点击数、花费、平台购买数和平台销售额。当前字段示例为：

```text
impressions / clicks / cost / purchases / sales
```

如需在 Campaign/Ad Group 层触发 R1、R2、R4、R6、R7，还需稳定的 Campaign 或 Ad Group 逻辑身份。当前字段示例为 `campaign_id`、`ad_group_id`；允许改名，但不得用 Touchpoint 身份冒充管理对象身份。

计算口径：

```text
CTR  = sum(clicks) / sum(impressions)
ACoS = sum(cost) / sum(sales)
ROAS = sum(sales) / sum(cost)
CVR  = sum(purchases) / sum(clicks)
```

要求：

- 分子与分母属于同一账户、币种、对象和窗口；
- 分母为 0 时返回不可计算；
- CPC 成本归 Click，CPM 成本归 Impression；
- Ads `purchases/sales` 是平台诊断指标；
- AMC `purchase_count/revenue` 是 Journey Outcome；
- MTA `attributed_revenue` 是归因结果；
- 三种购买/收入口径不得相加或互相替代；
- 只有 Impression 且直接 Sales 为 0 的 DSP 行，不应被直接判断为业务 ROAS=0。

### 3.4 MTA 输出

本体需要表达：

- Markov 与 Shapley 的完整五维 Touchpoint 结果；
- 购买用户、订单次数、收入三类 Outcome 的贡献结果；
- Touchpoint 的归因收入和归因 ROAS；
- 同一 Touchpoint + Outcome 的计算有效性、数据充分性和模型一致性；
- 报告窗口、账户、市场和币种。

当前字段示例包括 `converted_users`、`purchase_count`、`revenue`、`attributed_revenue`、`roas`、`calculation_valid`、`data_support_sufficient`、`models_consistent`。允许使用其他名称，但必须映射到这些本体逻辑概念。

模型一致性只表示当前数据和两个模型结论可比较，不代表因果证明，也不等于预测置信度或预算提案置信度。

### 3.5 Ground Truth（可选增强）

如提供 `simulation_ground_truth`：

- 只用于归因算法评估；
- 不进入训练特征、本体 Rule 输入或模型选择前的数据预处理；
- 必须与具体数据版本和模拟参数绑定；
- 结果只证明算法恢复模拟器机制的能力，不代表真实客户因果效果。

## 4. Demo 场景合同

模拟数据至少提供以下核心场景。每个场景需在批次说明中给出 `scenario_id`、对象 ID、计算窗口、预期指标和预期 Rule。

账户基准统一采用同账户、同币种、同窗口、同粒度对象的加权汇总：

```text
baseline_ctr  = sum(clicks) / sum(impressions)
baseline_acos = sum(cost) / sum(sales)
baseline_cvr  = sum(purchases) / sum(clicks)
baseline_mta_roas = sum(attributed_revenue) / sum(cost)
```

| 场景 | 可验收数据条件 | 预期 |
|---|---|---|
| R1 正例 | 14天 Campaign ACoS `>1.3×baseline_acos` 且 CTR `<0.6×baseline_ctr` | R1 触发 |
| R1 单条件负例 | 只满足 ACoS 或 CTR 条件 | R1 不触发 |
| R2 正例 | 最近7天曝光较前7天增长 `>20%`，建议生成 25%～35% | R2 触发 |
| R2 边界 | 曝光增长正好 `20%` | R2 不触发 |
| R3 正例 | Touchpoint MTA ROAS `>1.5×baseline_mta_roas` | R3 触发 |
| R3 边界 | Touchpoint MTA ROAS正好 `1.5×baseline_mta_roas` | R3 不触发 |
| R4 正例 | Campaign 14天聚合业务 ROAS `<1`，建议约 0.8 | R4 触发 |
| R4 边界 | Campaign 14天聚合业务 ROAS正好 `1` | R4 不触发 |
| R5 数据场景 | 指定 Touchpoint 的三类 Outcome 均有效、数据充分且 Markov/Shapley 一致 | 可派生 `attribution_consistency_status=consistent` |
| R6 正例 | Campaign 14天 CVR `<0.5×baseline_cvr`，建议为基准的30%～40% | R6 触发 |
| R6 边界 | Campaign 14天 CVR正好 `0.5×baseline_cvr` | R6 不触发 |
| R7 趋势场景 | 当前年度快照不具备未来预测输出 | R7 退役并返回 `NO_COVERAGE` |
| 正常对照 | 不满足任何业务 Rule 的触发条件 | R1/R2/R3/R4/R6均不触发 |
| 零分母 | 至少覆盖零 Impression、零 Click、零 Cost或零 Sales | 对应指标不可计算且不触发 |

说明：

- R5 的贡献份额、花费份额与归因差异由 MTA 输出直接提供或透明派生；
- R7 不再使用 Demo Mock 补足预测能力；
- 模拟数据不需要冒充预测模型或预算提案模块；
- 上表只要求核心数据覆盖，完整 Rule 正负例和护栏测试继续由本体断言表负责。

## 5. 批次与机器可读预期

每个交付批次至少表达接口版本、数据版本、批次身份、报告窗口、时区、账户、市场、币种、Touchpoint表达版本和场景预期。下面仅为一种机器可读示例，字段名和文件格式可以替换：

```json
{
  "dataset_version": "example-v1",
  "schema_version": "ontology-interface-v1",
  "batch_id": "example-batch-001",
  "report_start_date": "YYYY-MM-DD",
  "report_end_date": "YYYY-MM-DD",
  "timezone": "UTC",
  "marketplace": "US",
  "account_id": "adv_demo_001",
  "currency": "USD",
  "touchpoint_key_version": "four-plus-interaction-or-five",
  "scenario_expectations": [
    {
      "scenario_id": "R1_POS",
      "entity_type": "campaign",
      "entity_id": "example_campaign",
      "window_start": "YYYY-MM-DD",
      "window_end": "YYYY-MM-DD",
      "expected_rule": "R1",
      "expected_triggered": true
    }
  ]
}
```

文件名和内部生成方式可以调整，但上述语义必须可由机器读取或稳定映射。

同时提供字段映射，例如：

```json
{
  "physical_field": "campaignKey",
  "ontology_field": "campaign_id",
  "semantic_role": "campaign_identity",
  "data_type": "string",
  "grain": "campaign",
  "nullable": false
}
```

## 6. 本体组验收

收到数据后，本体组按以下结果验收：

1. 所有必需逻辑信息均有版本化字段映射，且声明含义、数据类型、粒度、主键、单位、时间口径和空值语义。
2. 无论物理字段如何命名，四个广告维度加互动类型都能确定性生成统一五维 Touchpoint 身份。
3. Path、Ads 表现和 MTA 输出中的 Touchpoint 集合能按约定规则对齐。
4. 同一计算批次不存在跨账户、跨币种、跨窗口或收入口径混用。
5. Campaign/Ad Group 指标可按声明窗口复算，分母为0时明确不可计算。
6. Markov、Shapley和模型一致性结果可按 `touchpoint + outcome` 读取。
7. 场景 Manifest 中每个核心场景的指标和触发预期可重复复算。
8. R1、R2、R3、R4、R6的正例、关键边界和正常对照符合预期。
9. R5 固定使用 `revenue` Outcome，并在同一批次、报告窗口和 Touchpoint 下读取贡献份额、派生花费份额及 Markov/Shapley 差异；任一键不一致或分母为0时返回 `NO_COVERAGE`。
10. R7 已退出当前 Demo；没有真实 campaign 级时间序列预测输出时必须返回 `NO_COVERAGE`，不得用年度 MTA 快照合成趋势。
11. 如交付用户事件，Journey、Outcome和14天边界案例符合第3.1～3.2节。
12. 同一版本重复生成或重复读取时，Schema、字段映射、场景对象和预期结果保持一致；含义或粒度改变时接口版本同步升级。

以上验收只判断数据是否满足本体 Demo，不评判模拟数据的内部实现方式。
