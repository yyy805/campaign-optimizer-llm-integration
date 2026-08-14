# Ontology Demo 断言表

这里保存 Demo 剧情的“标准答案”，不是客户观测数据，也不是完整日报。每个场景只记录足以判断规则、冲突、治理或状态变化的输入，以及系统必须产生的精确结果。

## 文件

- `assertion.schema.json`：结构契约。
- `story_assertions.json`：版本化场景清单。
- `check_assertions.py`：Schema、引用、规则条件、边界与覆盖校验。

## 两种校验

从仓库根目录使用 Ontology 项目自带虚拟环境运行：

```bash
docs/ontology/ontology脚本/.venv/bin/python "docs/ontology/ontology 概念卡/assertions/check_assertions.py" --contract-only
docs/ontology/ontology脚本/.venv/bin/python "docs/ontology/ontology 概念卡/assertions/check_assertions.py"
python3 -m json.tool "docs/ontology/ontology 概念卡/assertions/story_assertions.json"
```

`--contract-only` 用于第一阶段，只验证格式与所有已写场景的引用；完整模式还要求活跃规则 R1–R6、退役规则 R7 的 NO_COVERAGE、G1–G2、两个客户、边界、冲突、治理、生命周期、覆盖状态和快进分支全部存在。

## 编辑规则

1. 一个输入只表达聚合后或模型给出的判定值，不复制每日 CSV。
2. `triggered_rules` 是该实体在该时点的完整触发集合，不是“重点关注的规则”。
3. `demo_mock` 输入必须带 `mock_provenance`，明确写 `DEMO_ONLY_MOCK` 和 `production_evidence: false`。
4. `>`/`<` 的等号边界不触发；`>=`/`<=` 的等号边界触发。
5. 改规则条件或阈值时，先改已批准的规则卡，再同步正例、逐条件反例和边界场景。

## 后续衔接

剧情数据生成器可读取每个规则场景的 `inputs`，反推足够的日级 CSV，使窗口聚合值等于断言值。Golden Test 则应按 `assertion_id` 加载同一批 fixture，运行匹配、冲突裁决、治理和执行门控，并把实际结果与 `expected` 做精确比较。这样生成数据与回归测试共用同一份标准答案。
