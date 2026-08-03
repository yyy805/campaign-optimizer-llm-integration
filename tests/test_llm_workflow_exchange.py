"""生产级Workflow Exchange Gate的对抗性测试。"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import jsonschema
import pytest

from campaign_optimizer.contracts import (
    ContractValidationError,
    validate_answer_numeric_grounding,
    validate_authoritative_review,
    validate_contract_object,
    validate_workflow_exchange,
)
from campaign_optimizer.contracts.authority import (
    load_rule_card,
    latest_rule_version,
    public_rule_from_card,
)

ROOT = Path(__file__).parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "plan_a"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def exchange() -> dict[str, dict]:
    return {
        "request": _load("llm_request.demo.json"),
        "plan": _load("final_plan.demo.json"),
        "review": _load("ontology_review.demo.json"),
        "context": _load("llm_context.demo.json"),
        "output": _load("llm_workflow_output.demo.json"),
    }


def _validate(exchange: dict[str, dict]) -> None:
    validate_workflow_exchange(
        exchange["request"],
        exchange["plan"],
        exchange["review"],
        exchange["context"],
        exchange["output"],
    )


def test_golden_exchange_passes_production_gate(exchange):
    _validate(exchange)


def test_request_schema_rejects_excessive_history(exchange):
    request = copy.deepcopy(exchange["request"])
    request["mode"] = "chat"
    request["chat_history"] = [
        {"role": "user", "content": f"message-{index}"}
        for index in range(11)
    ]

    with pytest.raises(jsonschema.ValidationError):
        validate_contract_object("llm_request", request)


def test_system_fallback_is_allowed_for_initial_render(exchange):
    exchange["output"].update(
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

    _validate(exchange)


def test_exchange_rejects_context_id_mismatch(exchange):
    exchange["request"]["context_id"] = "context_other"

    with pytest.raises(ContractValidationError, match="context_id"):
        _validate(exchange)


def test_exchange_rejects_output_intent_not_allowed_by_request(exchange):
    exchange["output"]["intent"] = "EXPLAIN_PLAN"

    with pytest.raises(ContractValidationError, match="intent"):
        _validate(exchange)


def test_exchange_rejects_forged_deployment_versions(exchange):
    exchange["output"]["workflow_version"] = "forged"

    with pytest.raises(ContractValidationError, match="版本"):
        _validate(exchange)


def test_authority_rejects_caller_supplied_nonexistent_rule(exchange):
    item = exchange["review"]["items"][0]
    item.update({"rule_id": "R99", "rule_version": "1.0"})
    exchange["context"]["review_context"] = copy.deepcopy(exchange["review"])
    exchange["context"]["allowed_rule_ids"] = ["R99"]
    exchange["context"]["public_rule_context"] = [
        {
            "rule_id": "R99",
            "rule_version": "1.0",
            "status": "ACTIVE",
            "name": "伪造规则",
            "definition": "伪造定义",
            "applicable_scope": ["all"],
            "review_policy": {
                "mode": "review_only",
                "supported_plan_actions": [],
                "conflicting_plan_actions": [],
                "otherwise": "NOT_APPLICABLE",
            },
            "limitations": [],
        }
    ]

    with pytest.raises(ContractValidationError, match="不存在R99"):
        validate_authoritative_review(
            exchange["plan"], exchange["review"], exchange["context"]
        )


def test_authority_rejects_tampered_public_rule_definition(exchange):
    exchange["context"]["public_rule_context"][0]["definition"] = "调用方改写的定义"

    with pytest.raises(ContractValidationError, match="确定性导出"):
        validate_authoritative_review(
            exchange["plan"], exchange["review"], exchange["context"]
        )


def _make_r5_decisive(exchange: dict[str, dict]) -> None:
    item = exchange["review"]["items"][0]
    item.update(
        {
            "verdict": "CONFLICT",
            "base_confidence": 0.62,
            "runtime_confidence": 0.62,
            "missing_evidence": [],
            "missing_rule_parameters": [],
        }
    )
    exchange["review"]["overall_verdict"] = "CONFLICT"


def test_r5_increase_budget_must_be_conflict(exchange):
    _make_r5_decisive(exchange)
    validate_authoritative_review(
        exchange["plan"], exchange["review"], exchange["context"]
    )
    exchange["review"]["items"][0]["verdict"] = "SUPPORT"
    with pytest.raises(ContractValidationError, match="应为CONFLICT"):
        validate_authoritative_review(
            exchange["plan"], exchange["review"], exchange["context"]
        )


def test_authority_rejects_unrelated_fact_as_rule_support(exchange):
    _make_r5_decisive(exchange)
    fact = exchange["plan"]["review_evidence"][0]
    fact.update({"name": "random_metric", "value": 123, "unit": "count"})
    exchange["review"]["items"][0]["matched_fact_ids"] = [fact["fact_id"]]

    with pytest.raises(ContractValidationError, match="所需概念"):
        validate_authoritative_review(
            exchange["plan"], exchange["review"], exchange["context"]
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("unit", "count"),
        ("value", 1.5),
        ("value", "high"),
    ],
)
def test_authority_rejects_wrong_concept_unit_type_or_range(exchange, field, value):
    _make_r5_decisive(exchange)
    exchange["plan"]["review_evidence"][0][field] = value

    with pytest.raises(ContractValidationError, match="概念contribution_share"):
        validate_authoritative_review(
            exchange["plan"], exchange["review"], exchange["context"]
        )


def test_authority_rejects_wrong_evidence_period(exchange):
    _make_r5_decisive(exchange)
    exchange["plan"]["review_evidence"][0]["period"] = "2035_unrelated_snapshot"

    with pytest.raises(ContractValidationError, match="证据周期"):
        validate_authoritative_review(
            exchange["plan"], exchange["review"], exchange["context"]
        )


def test_authority_rejects_values_that_do_not_trigger_rule(exchange):
    _make_r5_decisive(exchange)
    exchange["plan"]["review_evidence"][0]["value"] = 0.99

    with pytest.raises(ContractValidationError, match="未命中R5触发条件"):
        validate_authoritative_review(
            exchange["plan"], exchange["review"], exchange["context"]
        )


def test_authority_rejects_rule_entity_grain_mismatch(exchange):
    _make_r5_decisive(exchange)
    exchange["plan"]["items"][0]["entity_type"] = "touchpoint"

    with pytest.raises(ContractValidationError, match="规则粒度channel"):
        validate_authoritative_review(
            exchange["plan"], exchange["review"], exchange["context"]
        )


def test_baseline_rule_requires_grounded_baseline(exchange):
    card = load_rule_card("R3")
    item = exchange["review"]["items"][0]
    item.update({
        "verdict": "SUPPORT",
        "rule_id": "R3",
        "rule_version": latest_rule_version(card),
        "base_confidence": 0.84,
        "runtime_confidence": 0.84,
        "matched_fact_ids": ["review_fact_001"],
    })
    exchange["review"]["overall_verdict"] = "SUPPORT"
    exchange["plan"]["items"][0]["entity_type"] = "touchpoint"
    fact = exchange["plan"]["review_evidence"][0]
    fact.update({
        "entity_type": "touchpoint",
        "name": "mta_roas",
        "value": 6.0,
        "unit": "ratio",
        "period": "current_snapshot",
    })
    exchange["context"]["public_rule_context"] = [public_rule_from_card(card)]

    with pytest.raises(ContractValidationError, match="baseline"):
        validate_authoritative_review(
            exchange["plan"], exchange["review"], exchange["context"]
        )
    fact.update({
        "baseline_value": 3.0,
        "baseline_source": "account_snapshot",
        "baseline_period": "current_snapshot",
    })
    validate_authoritative_review(
        exchange["plan"], exchange["review"], exchange["context"]
    )


def test_authority_rejects_zero_confidence_decisive_verdict(exchange):
    _make_r5_decisive(exchange)
    item = exchange["review"]["items"][0]
    item["base_confidence"] = 0
    item["runtime_confidence"] = 0

    with pytest.raises(ContractValidationError, match="必须大于0"):
        validate_authoritative_review(
            exchange["plan"], exchange["review"], exchange["context"]
        )


def test_numeric_grounding_rejects_15_percent_when_claim_is_10(exchange):
    exchange["output"]["answer"] = exchange["output"]["answer"].replace(
        "增加10%", "增加15%"
    )

    with pytest.raises(ContractValidationError, match="15.0%"):
        validate_answer_numeric_grounding(exchange["output"])


def test_action_percentage_cannot_be_grounded_by_confidence_claim(exchange):
    exchange["output"]["answer"] += " 如果改为增加62%，看起来也可行。"

    with pytest.raises(ContractValidationError, match="动作幅度增加62%"):
        validate_answer_numeric_grounding(exchange["output"])


def test_numeric_grounding_requires_budget_claim_values_in_answer(exchange):
    exchange["output"]["answer"] = exchange["output"]["answer"].replace(
        "1100美元", "推荐预算"
    )

    with pytest.raises(ContractValidationError, match="recommended_budget=1100"):
        validate_answer_numeric_grounding(exchange["output"])
