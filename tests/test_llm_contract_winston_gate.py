"""Winston最终Gate：证据绑定、规则去重和完整限制披露。"""
from __future__ import annotations

import copy
import json
from pathlib import Path

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


def test_review_cannot_borrow_fact_from_another_plan_item(bundle):
    second_item = {
        "plan_item_id": "plan_item_002",
        "entity_type": "channel",
        "entity_id": "Sponsored Brands",
        "action": "keep_budget",
        "delta_pct": 0,
        "current_budget": 800,
        "recommended_budget": 800,
        "currency": "USD",
    }
    second_fact = {
        "fact_id": "review_fact_004",
        "plan_item_id": "plan_item_002",
        "entity_type": "channel",
        "entity_id": "Sponsored Brands",
        "name": "contribution_share",
        "value": 0.2,
        "unit": "ratio",
        "period": "current_snapshot",
        "source": "demo_mta_output",
        "scope": "ontology_review",
    }
    bundle["plan"]["items"].append(second_item)
    bundle["plan"]["review_evidence"].append(second_fact)
    bundle["review"]["items"][0]["matched_fact_ids"] = ["review_fact_004"]
    bundle["context"]["plan_context"] = copy.deepcopy(bundle["plan"])
    bundle["context"]["review_context"] = copy.deepcopy(bundle["review"])
    bundle["context"]["allowed_plan_item_ids"].append("plan_item_002")
    bundle["context"]["allowed_fact_ids"].append("review_fact_004")

    with pytest.raises(ContractValidationError, match="不能跨方案条目"):
        validate_contract_bundle(
            bundle["plan"],
            bundle["review"],
            bundle["context"],
        )


def test_review_cannot_use_null_as_matched_fact(bundle):
    bundle["plan"]["review_evidence"][0]["value"] = None
    bundle["context"]["plan_context"] = copy.deepcopy(bundle["plan"])
    with pytest.raises(ContractValidationError, match="不能把空值作为命中证据"):
        validate_contract_bundle(
            bundle["plan"],
            bundle["review"],
            bundle["context"],
        )


def test_public_rule_context_rejects_conflicting_duplicate_version(bundle):
    duplicate = copy.deepcopy(bundle["context"]["public_rule_context"][0])
    duplicate["definition"] = "与第一份R5资料冲突的定义"
    bundle["context"]["public_rule_context"].append(duplicate)
    with pytest.raises(ContractValidationError, match="重复的规则版本"):
        validate_contract_bundle(
            bundle["plan"],
            bundle["review"],
            bundle["context"],
        )


def test_ok_output_must_claim_every_review_limitation(bundle):
    bundle["output"]["claims"] = [
        claim
        for claim in bundle["output"]["claims"]
        if claim["claim_id"] != "claim_012"
    ]
    with pytest.raises(ContractValidationError, match="完整披露全部限制claim"):
        validate_contract_bundle(
            bundle["plan"],
            bundle["review"],
            bundle["context"],
            bundle["output"],
        )
