"""John/Winston对抗审查确认后的L1回归测试。"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import jsonschema
import pytest

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
    return {
        "plan": _load("final_plan.demo.json"),
        "review": _load("ontology_review.demo.json"),
        "context": _load("llm_context.demo.json"),
        "output": _load("llm_workflow_output.demo.json"),
    }


def _sync_context(bundle: dict[str, dict]) -> None:
    bundle["context"]["plan_context"] = copy.deepcopy(bundle["plan"])
    bundle["context"]["review_context"] = copy.deepcopy(bundle["review"])
    bundle["context"]["allowed_plan_item_ids"] = [
        item["plan_item_id"] for item in bundle["plan"]["items"]
    ]


def _as_non_ok(output: dict, *, status: str, intent: str, answer: str) -> None:
    output.update(
        {
            "status": status,
            "intent": intent,
            "answer": answer,
            "claims": [],
            "facts_used": [],
            "rule_ids_used": [],
            "plan_item_ids_used": [],
            "limitations_included": False,
            "retry_count": 1 if status == "FALLBACK" else 0,
            "fallback_used": status == "FALLBACK",
        }
    )


def test_every_plan_item_must_receive_a_review(bundle):
    bundle["plan"]["items"].append(
        {
            "plan_item_id": "plan_item_002",
            "entity_type": "channel",
            "entity_id": "Display",
            "action": "keep_budget",
            "delta_pct": 0,
            "current_budget": 500,
            "recommended_budget": 500,
            "currency": "USD",
        }
    )
    _sync_context(bundle)

    with pytest.raises(ContractValidationError, match="必须覆盖每个plan_item"):
        validate_contract_bundle(
            bundle["plan"], bundle["review"], bundle["context"]
        )


def test_same_plan_and_rule_cannot_support_and_conflict(bundle):
    first = bundle["review"]["items"][0]
    first.update(
        {
            "verdict": "SUPPORT",
            "base_confidence": 0.7,
            "runtime_confidence": 0.7,
            "matched_fact_ids": ["review_fact_001"],
            "missing_evidence": [],
            "missing_rule_parameters": [],
        }
    )
    duplicate = copy.deepcopy(first)
    duplicate["review_item_id"] = "review_item_002"
    duplicate["verdict"] = "CONFLICT"
    bundle["review"]["items"].append(duplicate)
    bundle["review"]["overall_verdict"] = "CONFLICT"
    bundle["context"]["public_rule_context"][0]["status"] = "ACTIVE"
    _sync_context(bundle)

    with pytest.raises(ContractValidationError, match="组合只能有一条评价"):
        validate_contract_bundle(
            bundle["plan"], bundle["review"], bundle["context"]
        )


def test_set_budget_is_outside_l1_action_contract(bundle):
    plan = copy.deepcopy(bundle["plan"])
    plan["items"][0]["action"] = "set_budget"

    with pytest.raises(jsonschema.ValidationError):
        validate_contract_object("final_plan", plan)


def test_refused_output_must_use_fixed_backend_message(bundle):
    _as_non_ok(
        bundle["output"],
        status="REFUSED",
        intent="OUT_OF_SCOPE",
        answer="拒绝，但 Sponsored Products 当前预算1000，建议预算1100。",
    )

    with pytest.raises(ContractValidationError, match="必须使用后端固定文案"):
        validate_contract_bundle(
            bundle["plan"],
            bundle["review"],
            bundle["context"],
            bundle["output"],
        )


def test_fixed_refused_output_passes_runtime_gate(bundle):
    _as_non_ok(
        bundle["output"],
        status="REFUSED",
        intent="OUT_OF_SCOPE",
        answer="当前功能只能解释本次方案和本体评价。",
    )

    validate_contract_bundle(
        bundle["plan"],
        bundle["review"],
        bundle["context"],
        bundle["output"],
    )


def test_used_ids_must_be_backed_by_claims(bundle):
    bundle["output"]["claims"] = [
        claim
        for claim in bundle["output"]["claims"]
        if claim["source_id"] != "review_fact_003"
    ]

    with pytest.raises(ContractValidationError, match="facts_used必须严格等于"):
        validate_contract_bundle(
            bundle["plan"],
            bundle["review"],
            bundle["context"],
            bundle["output"],
        )
