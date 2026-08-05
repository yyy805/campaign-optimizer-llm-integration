"""权威规则卡发布前置条件测试。"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import jsonschema
import pytest

from campaign_optimizer.contracts.authority import (
    latest_rule_version,
    public_rule_from_card,
)
from campaign_optimizer.contracts.validation import ContractValidationError

ROOT = Path(__file__).parent.parent
RULES_DIR = ROOT / "campaign_optimizer" / "ontology" / "rules"
RULE_SCHEMA = ROOT / "campaign_optimizer" / "ontology" / "schemas" / "rule.schema.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", sorted(RULES_DIR.glob("R*.json")))
def test_every_rule_card_has_publishable_version_and_public_projection(path):
    card = _load(path)
    jsonschema.Draft7Validator(
        _load(RULE_SCHEMA), format_checker=jsonschema.FormatChecker()
    ).validate(card)

    public = public_rule_from_card(card)
    assert public["rule_id"] == card["rule_id"]
    assert public["rule_version"] == latest_rule_version(card)
    assert public["status"] == card["status"]
    assert public["review_policy"] == card["review_policy"]


def test_empty_version_history_is_rejected_before_publication():
    card = _load(RULES_DIR / "R3.json")
    card["version_history"] = []

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft7Validator(_load(RULE_SCHEMA)).validate(card)
    with pytest.raises(ContractValidationError, match="version_history"):
        latest_rule_version(card)


def test_active_rule_rejects_unknown_trigger_shape():
    card = _load(RULES_DIR / "R5.json")
    card["trigger_condition"] = {"bogus": True}

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.Draft7Validator(_load(RULE_SCHEMA)).validate(card)


def test_public_projection_does_not_accept_caller_rewording():
    card = _load(RULES_DIR / "R5.json")
    expected = public_rule_from_card(card)
    tampered = copy.deepcopy(expected)
    tampered["definition"] = "调用方自行改写的定义"

    assert tampered != expected


def test_all_rules_are_review_only_and_contain_no_auto_execution_policy():
    for path in sorted(RULES_DIR.glob("R*.json")):
        card = _load(path)
        policy = card["review_policy"]
        assert policy["mode"] == "review_only"
        assert not set(policy["supported_plan_actions"]).intersection(
            policy["conflicting_plan_actions"]
        )
        assert "execution_policy" not in card
        assert "risk_model" not in card


def test_current_r5_is_pending_and_historical_r5_is_immutable():
    r5 = _load(RULES_DIR / "R5.json")
    historical_r5 = _load(
        RULES_DIR.parent / 'history' / 'rules' / 'R5.touchpoint.1.3-contract-hardening.json'
    )
    r7 = _load(RULES_DIR / "R7.json")
    assert r5['status'] == 'PENDING_HUMAN_REVIEW'
    assert latest_rule_version(r5) == '2.0-campaign-pending'
    assert r5['review_policy']['conflicting_plan_actions'] == []
    assert historical_r5['status'] == 'ACTIVE'
    assert latest_rule_version(historical_r5) == '1.3-contract-hardening'
    assert historical_r5['review_policy']['conflicting_plan_actions'] == ['increase_budget']
    assert r7["status"] == "RETIRED"
    assert latest_rule_version(r7) == "1.3-contract-hardening"
    assert r7["match_inputs"] == []
