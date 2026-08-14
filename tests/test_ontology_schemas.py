"""
pytest 测试：S0.1 验收——概念卡/规则卡 schema 校验。

覆盖：
  - 示例卡（roas.json / R3.json）通过各自 schema 校验；
  - 故意删一个必填字段，校验必须报错（证明 schema 真的在拦，而不是形同虚设）。
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import jsonschema
import pytest

ONTOLOGY_DIR = Path(__file__).parent.parent / "campaign_optimizer" / "ontology"
SCHEMAS_DIR = ONTOLOGY_DIR / "schemas"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def concept_schema() -> dict:
    return _load(SCHEMAS_DIR / "concept.schema.json")


@pytest.fixture
def rule_schema() -> dict:
    return _load(SCHEMAS_DIR / "rule.schema.json")


@pytest.fixture
def roas_card() -> dict:
    return _load(ONTOLOGY_DIR / "concepts" / "roas.json")


@pytest.fixture
def r3_card() -> dict:
    return _load(ONTOLOGY_DIR / "rules" / "R3.json")


# ---------------------------------------------------------------------------
# 示例卡通过校验
# ---------------------------------------------------------------------------

def test_roas_card_passes_concept_schema(concept_schema, roas_card):
    jsonschema.validate(instance=roas_card, schema=concept_schema)


def test_r3_card_passes_rule_schema(rule_schema, r3_card):
    jsonschema.validate(instance=r3_card, schema=rule_schema)


# ---------------------------------------------------------------------------
# 少填一个必填字段，schema 必须报错
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "missing_field",
    ["concept_id", "caliber", "granularity", "dimensions", "value_range"],
)
def test_concept_schema_rejects_missing_required_field(concept_schema, roas_card, missing_field):
    broken = copy.deepcopy(roas_card)
    del broken[missing_field]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=broken, schema=concept_schema)


@pytest.mark.parametrize(
    "missing_field",
    ["rule_id", "evaluation_grain", "attribution_model", "risk_level", "status"],
)
def test_rule_schema_rejects_missing_required_field(rule_schema, r3_card, missing_field):
    broken = copy.deepcopy(r3_card)
    del broken[missing_field]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=broken, schema=rule_schema)


def test_concept_schema_rejects_unknown_field(concept_schema, roas_card):
    """additionalProperties: false 要能拦住手滑多打的字段（比如把 caliber 错拼成 calibre）。"""
    broken = copy.deepcopy(roas_card)
    broken["not_a_real_field"] = "typo"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=broken, schema=concept_schema)


# ---------------------------------------------------------------------------
# 字段存在，但值是非法枚举——比漏填更容易被人手滑犯下、也更容易蒙混过关
# （Murat 审查意见：这类"看着填了、其实填错"的情况之前没有测试覆盖）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "field,bad_value",
    [
        ("caliber", "Platform"),  # 大小写错了
        ("tier", "primary"),  # 不在 base/derived/mta 里
        ("layer", "L7"),  # 超出 L1-L6/R
    ],
)
def test_concept_schema_rejects_illegal_enum_value(concept_schema, roas_card, field, bad_value):
    broken = copy.deepcopy(roas_card)
    broken[field] = bad_value
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=broken, schema=concept_schema)


@pytest.mark.parametrize(
    "bad_concept_id",
    ["ROAS", "1roas", "ro as", "roas!"],
)
def test_concept_schema_rejects_malformed_concept_id(concept_schema, roas_card, bad_concept_id):
    broken = copy.deepcopy(roas_card)
    broken["concept_id"] = bad_concept_id
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=broken, schema=concept_schema)


@pytest.mark.parametrize(
    "field,bad_value",
    [
        ("risk_level", "CRITICAL"),  # 不在 LOW/MEDIUM/HIGH 里
        ("evidence_type", "guess"),  # 不在四级证据类型里
        ("status", "DELETED"),  # 不在合法状态里
    ],
)
def test_rule_schema_rejects_illegal_enum_value(rule_schema, r3_card, field, bad_value):
    broken = copy.deepcopy(r3_card)
    broken[field] = bad_value
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=broken, schema=rule_schema)


@pytest.mark.parametrize(
    "bad_rule_id",
    ["r3", "R", "Rule3", "3"],
)
def test_rule_schema_rejects_malformed_rule_id(rule_schema, r3_card, bad_rule_id):
    broken = copy.deepcopy(r3_card)
    broken["rule_id"] = bad_rule_id
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=broken, schema=rule_schema)


def test_every_concept_card_passes_schema_and_relations_resolve(concept_schema):
    cards = {
        path.stem: _load(path)
        for path in (ONTOLOGY_DIR / "concepts").glob("*.json")
    }
    assert len(cards) == 24
    for concept_id, card in cards.items():
        jsonschema.validate(instance=card, schema=concept_schema)
        assert card["concept_id"] == concept_id
        for relation in card["related_concepts"]:
            assert relation["target"] in cards


def test_concept_schema_supports_string_enum(concept_schema, roas_card):
    card = copy.deepcopy(roas_card)
    card["value_type"] = "string_enum"
    card["value_range"] = None
    card["allowed_values"] = ["HIGH", "LOW"]
    card["threshold"] = None
    jsonschema.validate(instance=card, schema=concept_schema)


def test_boolean_concept_rejects_numeric_range(concept_schema):
    card = _load(ONTOLOGY_DIR / "concepts" / "roas_decline_alert.json")
    broken = copy.deepcopy(card)
    broken["value_range"] = [0, 1]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=broken, schema=concept_schema)


def test_every_guardrail_is_review_only_and_schema_valid():
    schema = _load(SCHEMAS_DIR / "guardrail.schema.json")
    for path in (ONTOLOGY_DIR / "guardrails").glob("G*.json"):
        card = _load(path)
        jsonschema.validate(instance=card, schema=schema)
        assert card["on_violation"] not in {"block_auto_execution", "reject"}


def test_g1_and_g2_wait_for_the_final_plan_input_contract():
    for guardrail_id in ("G1", "G2"):
        card = _load(ONTOLOGY_DIR / "guardrails" / f"{guardrail_id}.json")
        assert card["status"] == "PENDING_INPUT_CONTRACT"
        assert card["condition"] is None
        assert card["applies_to_plan_actions"] == []
