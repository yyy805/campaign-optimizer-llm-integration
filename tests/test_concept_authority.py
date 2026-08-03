"""规则引用概念的权威完整性测试。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from campaign_optimizer.contracts.concept_authority import (
    collect_rule_concepts,
    load_concept_card,
    validate_rule_fact_semantics,
)

ROOT = Path(__file__).parent.parent
RULES_DIR = ROOT / "campaign_optimizer" / "ontology" / "rules"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", sorted(RULES_DIR.glob("R*.json")))
def test_every_rule_concept_has_an_authoritative_concept_card(path):
    rule = _load(path)
    concept_ids = collect_rule_concepts(rule["trigger_condition"])
    if rule["status"] == "RETIRED":
        assert concept_ids == set()
        return
    assert concept_ids
    for concept_id in concept_ids:
        card = load_concept_card(concept_id)
        assert card["concept_id"] == concept_id
        assert card["unit"]


def test_ratio_concept_rejects_wrong_unit_and_out_of_range_value():
    trigger = {"all": [{"concept": "contribution_share", "op": ">", "ref": 0.1}]}
    fact = {
        "fact_id": "review_fact_test",
        "name": "contribution_share",
        "value": 1.5,
        "unit": "count",
    }

    errors = validate_rule_fact_semantics(trigger, [fact])

    assert any("单位" in error for error in errors)
    assert any("上限1" in error for error in errors)


def test_boolean_concept_rejects_number_value():
    trigger = {
        "all": [{"concept": "roas_decline_alert", "op": "==", "ref": True}]
    }
    fact = {
        "fact_id": "review_fact_bool", "name": "roas_decline_alert",
        "value": 1, "unit": "boolean",
    }
    errors = validate_rule_fact_semantics(trigger, [fact])
    assert any("must be boolean" in error for error in errors)
