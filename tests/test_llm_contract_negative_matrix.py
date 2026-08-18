"""跨ID、白名单和嵌套对象的P0负向矩阵。"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import jsonschema
import pytest
from tests.active_rule_fixture import active_rule_bundle

from campaign_optimizer.contracts.validation import (
    ContractValidationError,
    validate_contract_bundle,
    validate_contract_object,
)

ROOT = Path(__file__).parent.parent
FIXTURES_DIR = ROOT / "tests" / "fixtures" / "plan_a"


def _load(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


@pytest.fixture
def bundle() -> dict[str, dict]:
    return active_rule_bundle()
    return {
        "plan": _load("final_plan.demo.json"),
        "review": _load("ontology_review.demo.json"),
        "context": _load("llm_context.demo.json"),
        "output": _load("llm_workflow_output.demo.json"),
    }


def _validate(bundle: dict[str, dict]) -> None:
    validate_contract_bundle(
        bundle["plan"],
        bundle["review"],
        bundle["context"],
        bundle["output"],
    )


def _second_plan_item() -> dict:
    return {
        "plan_item_id": "plan_item_002",
        "entity_type": "campaign",
        "entity_id": "Sponsored Brands",
        "action": "keep_budget",
        "delta_pct": 0,
        "current_budget": 800,
        "recommended_budget": 800,
        "currency": "USD",
    }


def test_duplicate_plan_item_id_is_rejected(bundle):
    duplicate = copy.deepcopy(bundle["plan"]["items"][0])
    duplicate["entity_id"] = "A different channel"
    bundle["plan"]["items"].append(duplicate)
    bundle["context"]["plan_context"] = copy.deepcopy(bundle["plan"])
    with pytest.raises(ContractValidationError, match="plan_item_id必须唯一"):
        _validate(bundle)


def test_duplicate_fact_id_is_rejected(bundle):
    duplicate = copy.deepcopy(bundle["plan"]["review_evidence"][0])
    duplicate["source"] = "a_conflicting_source"
    bundle["plan"]["review_evidence"].append(duplicate)
    bundle["context"]["plan_context"] = copy.deepcopy(bundle["plan"])
    with pytest.raises(ContractValidationError, match="fact_id必须唯一"):
        _validate(bundle)


def test_duplicate_review_item_id_is_rejected(bundle):
    duplicate = copy.deepcopy(bundle["review"]["items"][0])
    duplicate["limitations"].append("另一条限制")
    bundle["review"]["items"].append(duplicate)
    bundle["context"]["review_context"] = copy.deepcopy(bundle["review"])
    with pytest.raises(ContractValidationError, match="review_item_id必须唯一"):
        _validate(bundle)


def test_duplicate_claim_id_is_rejected(bundle):
    duplicate = copy.deepcopy(bundle["output"]["claims"][0])
    duplicate["field"] = "action"
    duplicate["value"] = "increase_budget"
    bundle["output"]["claims"].append(duplicate)
    with pytest.raises(ContractValidationError, match="claim_id必须唯一"):
        _validate(bundle)


@pytest.mark.parametrize(
    "mutation,error",
    [
        ("unknown_plan_item", "引用不存在的plan_item_id"),
        ("entity_mismatch", "实体与方案条目不一致"),
    ],
)
def test_fact_binding_failures_are_rejected(bundle, mutation, error):
    fact = bundle["plan"]["review_evidence"][0]
    if mutation == "unknown_plan_item":
        fact["plan_item_id"] = "plan_item_999"
    else:
        fact["entity_id"] = "Another channel"
    bundle["context"]["plan_context"] = copy.deepcopy(bundle["plan"])
    with pytest.raises(ContractValidationError, match=error):
        _validate(bundle)


def test_review_plan_id_mismatch_is_rejected(bundle):
    bundle["review"]["plan_id"] = "plan_other"
    bundle["context"]["review_context"] = copy.deepcopy(bundle["review"])
    with pytest.raises(ContractValidationError, match="plan_id与final_plan"):
        _validate(bundle)


def test_review_item_unknown_plan_item_is_rejected(bundle):
    bundle["review"]["items"][0]["plan_item_id"] = "plan_item_999"
    bundle["context"]["review_context"] = copy.deepcopy(bundle["review"])
    with pytest.raises(ContractValidationError, match="引用不存在的plan_item_id"):
        _validate(bundle)


@pytest.mark.parametrize("snapshot", ["plan_context", "review_context"])
def test_context_must_contain_exact_read_only_snapshots(bundle, snapshot):
    if snapshot == "plan_context":
        bundle["context"][snapshot]["source_version"] = "tampered"
        error = "plan_context不是"
    else:
        bundle["context"][snapshot]["ontology_version"] = "tampered"
        error = "review_context不是"
    with pytest.raises(ContractValidationError, match=error):
        _validate(bundle)


@pytest.mark.parametrize(
    "field,bad_value,error",
    [
        (
            "allowed_plan_item_ids",
            ["plan_item_001", "plan_item_999"],
            "allowed_plan_item_ids",
        ),
        (
            "allowed_fact_ids",
            ["decision_fact_001"],
            "allowed_fact_ids",
        ),
        (
            "allowed_fact_ids",
            [
                "decision_fact_001",
                "review_fact_001",
                "review_fact_002",
                "review_fact_003",
                "review_fact_999",
            ],
            "allowed_fact_ids",
        ),
        ("allowed_rule_ids", [], "allowed_rule_ids"),
    ],
)
def test_whitelists_must_equal_derived_sets(bundle, field, bad_value, error):
    bundle["context"][field] = bad_value
    with pytest.raises(ContractValidationError, match=error):
        _validate(bundle)


def test_allowed_plan_item_ids_cannot_omit_a_real_item(bundle):
    bundle["plan"]["items"].append(_second_plan_item())
    bundle["context"]["plan_context"] = copy.deepcopy(bundle["plan"])
    with pytest.raises(ContractValidationError, match="allowed_plan_item_ids"):
        _validate(bundle)


@pytest.mark.parametrize("mutation", ["missing", "extra", "wrong_version"])
def test_public_rule_context_must_exactly_cover_reviewed_versions(bundle, mutation):
    if mutation == "missing":
        bundle["context"]["public_rule_context"] = []
    elif mutation == "extra":
        extra = copy.deepcopy(bundle["context"]["public_rule_context"][0])
        extra["rule_id"] = "R99"
        extra["rule_version"] = "1.0"
        bundle["context"]["public_rule_context"].append(extra)
    else:
        bundle["context"]["public_rule_context"][0]["rule_version"] = "wrong-version"
    with pytest.raises(ContractValidationError, match="public_rule_context"):
        _validate(bundle)


@pytest.mark.parametrize(
    "name,mutator",
    [
        ("final_plan", lambda value: value["period"].update(extra="typo")),
        (
            "final_plan",
            lambda value: value["review_evidence"][0].update(extra="typo"),
        ),
        (
            "llm_context",
            lambda value: value["plan_context"]["period"].update(extra="typo"),
        ),
        (
            "llm_context",
            lambda value: value["review_context"]["items"][0].update(extra="typo"),
        ),
    ],
)
def test_nested_unknown_fields_are_rejected(name, mutator, bundle):
    value = copy.deepcopy(
        bundle["context"] if name == "llm_context" else bundle["plan"]
    )
    mutator(value)
    with pytest.raises(jsonschema.ValidationError):
        validate_contract_object(name, value)
