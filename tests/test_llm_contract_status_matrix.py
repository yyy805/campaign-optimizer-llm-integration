"""Workflow状态单变量负向矩阵，避免多个错误互相遮蔽。"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import jsonschema
import pytest

from campaign_optimizer.contracts.validation import validate_contract_object

ROOT = Path(__file__).parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "plan_a" / "llm_workflow_output.demo.json"


def _golden() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _refused() -> dict:
    value = _golden()
    value.update(
        {
            "status": "REFUSED",
            "intent": "OUT_OF_SCOPE",
            "answer": "当前问题不在支持范围内。",
            "claims": [],
            "facts_used": [],
            "rule_ids_used": [],
            "plan_item_ids_used": [],
            "limitations_included": False,
            "retry_count": 0,
            "fallback_used": False,
        }
    )
    return value


def _fallback() -> dict:
    value = _golden()
    value.update(
        {
            "status": "FALLBACK",
            "intent": "SYSTEM_FALLBACK",
            "answer": "解释服务暂时不可用，请稍后重试。",
            "claims": [],
            "facts_used": [],
            "rule_ids_used": [],
            "plan_item_ids_used": [],
            "limitations_included": False,
            "retry_count": 1,
            "fallback_used": True,
        }
    )
    return value


@pytest.mark.parametrize(
    "field,bad_value",
    [
        ("intent", "EXPLAIN_REVIEW"),
        ("claims", [_golden()["claims"][0]]),
        ("facts_used", ["decision_fact_001"]),
        ("rule_ids_used", ["R5"]),
        ("plan_item_ids_used", ["plan_item_001"]),
        ("limitations_included", True),
        ("retry_count", 1),
        ("fallback_used", True),
    ],
)
def test_refused_rejects_each_invalid_field_independently(field, bad_value):
    output = _refused()
    output[field] = copy.deepcopy(bad_value)
    with pytest.raises(jsonschema.ValidationError):
        validate_contract_object("llm_workflow_output", output)


@pytest.mark.parametrize(
    "field,bad_value",
    [
        ("intent", "EXPLAIN_REVIEW"),
        ("claims", [_golden()["claims"][0]]),
        ("facts_used", ["decision_fact_001"]),
        ("rule_ids_used", ["R5"]),
        ("plan_item_ids_used", ["plan_item_001"]),
        ("limitations_included", True),
        ("retry_count", 0),
        ("fallback_used", False),
    ],
)
def test_fallback_rejects_each_invalid_field_independently(field, bad_value):
    output = _fallback()
    output[field] = copy.deepcopy(bad_value)
    with pytest.raises(jsonschema.ValidationError):
        validate_contract_object("llm_workflow_output", output)


def test_refused_and_fallback_valid_baselines():
    validate_contract_object("llm_workflow_output", _refused())
    validate_contract_object("llm_workflow_output", _fallback())
