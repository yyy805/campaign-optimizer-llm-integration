"""L1运行时Gate：多条目、多规则、verdict组合和动作一致性。"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import jsonschema
import pytest

from campaign_optimizer.contracts.validation import (
    ContractValidationError,
    validate_contract_bundle,
)

ROOT = Path(__file__).parent.parent
FIXTURES_DIR = ROOT / "tests" / "fixtures" / "plan_a"


def _load(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


@pytest.fixture
def bundle() -> dict[str, dict]:
    return {
        "plan": _load("final_plan.demo.json"),
        "review": _load("ontology_review.demo.json"),
        "context": _load("llm_context.demo.json"),
        "output": _load("llm_workflow_output.demo.json"),
    }


def _public_rule(rule_id: str, version: str, status: str = "ACTIVE") -> dict:
    return {
        "rule_id": rule_id,
        "rule_version": version,
        "status": status,
        "name": f"{rule_id}测试规则",
        "definition": "仅用于L1契约分支测试。",
        "applicable_scope": ["Demo契约测试"],
        "review_policy": {
            "mode": "review_only",
            "supported_plan_actions": ["increase_budget"],
            "conflicting_plan_actions": ["decrease_budget"],
            "otherwise": "NOT_APPLICABLE",
        },
        "limitations": [],
    }


def _rebuild_context(
    context: dict,
    plan: dict,
    review: dict,
    public_rules: list[dict],
) -> None:
    context["plan_context"] = copy.deepcopy(plan)
    context["review_context"] = copy.deepcopy(review)
    context["allowed_plan_item_ids"] = [
        item["plan_item_id"] for item in plan["items"]
    ]
    context["allowed_fact_ids"] = [
        fact["fact_id"]
        for fact in plan["decision_evidence"] + plan["review_evidence"]
    ]
    context["allowed_rule_ids"] = sorted(
        {
            item["rule_id"]
            for item in review["items"]
            if item["rule_id"] is not None
        }
    )
    context["public_rule_context"] = public_rules


def _set_single_verdict(
    bundle: dict[str, dict],
    verdict: str,
    *,
    rule_status: str = "ACTIVE",
) -> None:
    item = bundle["review"]["items"][0]
    item["verdict"] = verdict
    bundle["review"]["overall_verdict"] = verdict

    if verdict in {"SUPPORT", "CONFLICT", "NOT_APPLICABLE"}:
        item["base_confidence"] = 0.5
        item["runtime_confidence"] = 0.5
        item["missing_evidence"] = []
        item["missing_rule_parameters"] = []
    elif verdict == "UNVERIFIED":
        item["rule_id"] = None
        item["rule_version"] = None
        item["base_confidence"] = None
        item["runtime_confidence"] = None
        item["matched_fact_ids"] = []
        item["missing_evidence"] = []
        item["missing_rule_parameters"] = []

    rules = []
    if item["rule_id"] is not None:
        rules.append(_public_rule(item["rule_id"], item["rule_version"], rule_status))
    _rebuild_context(bundle["context"], bundle["plan"], bundle["review"], rules)


@pytest.mark.parametrize("verdict", ["SUPPORT", "CONFLICT", "UNVERIFIED"])
def test_legal_verdict_combinations_pass(bundle, verdict):
    _set_single_verdict(bundle, verdict)
    validate_contract_bundle(bundle["plan"], bundle["review"], bundle["context"])


@pytest.mark.parametrize("verdict", ["SUPPORT", "CONFLICT"])
def test_decisive_verdict_requires_active_rule(bundle, verdict):
    _set_single_verdict(bundle, verdict, rule_status="PENDING_HUMAN_REVIEW")
    with pytest.raises(ContractValidationError, match="只能引用ACTIVE规则"):
        validate_contract_bundle(bundle["plan"], bundle["review"], bundle["context"])


@pytest.mark.parametrize("verdict", ["SUPPORT", "CONFLICT"])
def test_decisive_verdict_requires_matched_review_fact(bundle, verdict):
    _set_single_verdict(bundle, verdict)
    bundle["review"]["items"][0]["matched_fact_ids"] = []
    _rebuild_context(
        bundle["context"],
        bundle["plan"],
        bundle["review"],
        [_public_rule("R5", "1.3-contract-hardening")],
    )
    with pytest.raises(jsonschema.ValidationError):
        validate_contract_bundle(bundle["plan"], bundle["review"], bundle["context"])


def test_unverified_rejects_rule_reference(bundle):
    _set_single_verdict(bundle, "UNVERIFIED")
    bundle["review"]["items"][0]["rule_id"] = "R5"
    with pytest.raises(jsonschema.ValidationError):
        validate_contract_bundle(bundle["plan"], bundle["review"], bundle["context"])


def test_evidence_containers_reject_wrong_fact_prefix_and_scope(bundle):
    misplaced = copy.deepcopy(bundle["plan"]["decision_evidence"][0])
    bundle["plan"]["review_evidence"][0] = misplaced
    bundle["context"]["plan_context"] = copy.deepcopy(bundle["plan"])
    with pytest.raises(jsonschema.ValidationError):
        validate_contract_bundle(bundle["plan"], bundle["review"], bundle["context"])


def test_ontology_review_cannot_match_decision_fact(bundle):
    bundle["review"]["items"][0]["matched_fact_ids"] = ["decision_fact_001"]
    bundle["context"]["review_context"] = copy.deepcopy(bundle["review"])
    with pytest.raises(jsonschema.ValidationError):
        validate_contract_bundle(bundle["plan"], bundle["review"], bundle["context"])


@pytest.mark.parametrize(
    "action,delta,current,recommended",
    [
        ("increase_budget", -10, 1000, 900),
        ("decrease_budget", 10, 1000, 1100),
        ("keep_budget", 10, 1000, 1100),
        ("increase_budget", 10, 1000, 1090),
    ],
)
def test_action_direction_and_budget_math_are_enforced(
    bundle, action, delta, current, recommended
):
    item = bundle["plan"]["items"][0]
    item.update(
        {
            "action": action,
            "delta_pct": delta,
            "current_budget": current,
            "recommended_budget": recommended,
        }
    )
    bundle["context"]["plan_context"] = copy.deepcopy(bundle["plan"])
    with pytest.raises(ContractValidationError):
        validate_contract_bundle(bundle["plan"], bundle["review"], bundle["context"])


def test_multiple_plan_items_and_mixed_verdict_use_conservative_priority(bundle):
    second_item = {
        "plan_item_id": "plan_item_002",
        "entity_type": "campaign",
        "entity_id": "Sponsored Brands",
        "action": "decrease_budget",
        "delta_pct": -5,
        "current_budget": 800,
        "recommended_budget": 760,
        "currency": "USD",
    }
    second_fact = {
        "fact_id": "review_fact_004",
        "plan_item_id": "plan_item_002",
        "entity_type": "campaign",
        "entity_id": "Sponsored Brands",
        "name": "roas",
        "value": 1.2,
        "unit": "ratio",
        "period": "current_14_days",
        "source": "demo_platform_output",
        "scope": "ontology_review",
    }
    second_review = {
        "review_item_id": "review_item_002",
        "plan_item_id": "plan_item_002",
        "verdict": "CONFLICT",
        "rule_id": "R1",
        "rule_version": "1.0-demo",
        "base_confidence": 0.4,
        "runtime_confidence": 0.4,
        "matched_fact_ids": ["review_fact_004"],
        "missing_evidence": [],
        "missing_rule_parameters": [],
        "limitations": [],
    }
    bundle["plan"]["items"].append(second_item)
    bundle["plan"]["review_evidence"].append(second_fact)
    bundle["review"]["items"].append(second_review)
    bundle["review"]["overall_verdict"] = "CONFLICT"
    rules = [
        _public_rule("R5", "1.3-contract-hardening"),
        _public_rule("R1", "1.0-demo"),
    ]
    _rebuild_context(bundle["context"], bundle["plan"], bundle["review"], rules)
    validate_contract_bundle(bundle["plan"], bundle["review"], bundle["context"])


def test_one_plan_item_can_be_reviewed_by_multiple_rules(bundle):
    second_review = {
        "review_item_id": "review_item_002",
        "plan_item_id": "plan_item_001",
        "verdict": "SUPPORT",
        "rule_id": "R1",
        "rule_version": "1.0-demo",
        "base_confidence": 0.4,
        "runtime_confidence": 0.4,
        "matched_fact_ids": ["review_fact_001"],
        "missing_evidence": [],
        "missing_rule_parameters": [],
        "limitations": [],
    }
    bundle["review"]["items"].append(second_review)
    bundle["review"]["overall_verdict"] = "CONFLICT"
    rules = [
        _public_rule("R5", "1.3-contract-hardening"),
        _public_rule("R1", "1.0-demo"),
    ]
    _rebuild_context(bundle["context"], bundle["plan"], bundle["review"], rules)
    validate_contract_bundle(bundle["plan"], bundle["review"], bundle["context"])


def test_same_rule_cannot_have_two_versions_in_one_review(bundle):
    second_review = copy.deepcopy(bundle["review"]["items"][0])
    second_review["review_item_id"] = "review_item_002"
    second_review["rule_version"] = "0.2-skeleton"
    bundle["review"]["items"].append(second_review)
    _rebuild_context(
        bundle["context"],
        bundle["plan"],
        bundle["review"],
        [
            _public_rule("R5", "1.3-contract-hardening"),
            _public_rule("R5", "0.2-skeleton", "PENDING_HUMAN_REVIEW"),
        ],
    )
    with pytest.raises(ContractValidationError, match="一个rule_id只能对应一个rule_version"):
        validate_contract_bundle(bundle["plan"], bundle["review"], bundle["context"])


def test_golden_output_discloses_version_and_time_grain_limit(bundle):
    answer = bundle["output"]["answer"]
    assert "R5 1.3-contract-hardening" in answer
    assert "时间粒度尚未完全对齐" in answer
    validate_contract_bundle(
        bundle["plan"],
        bundle["review"],
        bundle["context"],
        bundle["output"],
    )


def test_claim_must_be_synchronized_with_used_ids(bundle):
    bundle["output"]["rule_ids_used"] = []
    with pytest.raises(ContractValidationError, match="未同步rule_ids_used"):
        validate_contract_bundle(
            bundle["plan"],
            bundle["review"],
            bundle["context"],
            bundle["output"],
        )
