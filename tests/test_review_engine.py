from __future__ import annotations

import copy
import json
import math
import subprocess
import sys
from pathlib import Path

import pytest

from campaign_optimizer.contracts.authority import (
    load_rule_card,
    public_rule_from_card,
    validate_authoritative_review,
)
from campaign_optimizer.contracts.validation import ContractValidationError
from campaign_optimizer.ontology.review_engine import generate_ontology_review

ROOT = Path(__file__).parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "plan_a" / "final_plan.demo.json"
STATE_FIXTURE = ROOT / "tests" / "fixtures" / "plan_a" / "confidence_state.r5.demo.json"


def _plan() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _state() -> dict:
    return json.loads(STATE_FIXTURE.read_text(encoding="utf-8"))


def _generate(plan: dict, **kwargs) -> dict:
    if "confidence_states" not in kwargs:
        kwargs["confidence_states"] = {"R5": _state()}
    return generate_ontology_review(
        plan,
        ontology_version="test-ontology-v1",
        confidence_state_version="test-confidence-v1",
        **kwargs,
    )


def _set_action(plan: dict, action: str) -> None:
    item = plan["items"][0]
    item["action"] = action
    if action == "increase_budget":
        item.update(delta_pct=10, recommended_budget=1100)
    elif action == "decrease_budget":
        item.update(delta_pct=-10, recommended_budget=900)
    else:
        item.update(delta_pct=0, recommended_budget=1000)


@pytest.mark.parametrize(
    "action,verdict",
    [("increase_budget", "CONFLICT"), ("decrease_budget", "SUPPORT"),
     ("keep_budget", "NOT_APPLICABLE")],
)
def test_r5_maps_matched_rule_to_plan_action(action, verdict):
    plan = _plan()
    _set_action(plan, action)
    review = _generate(plan)
    assert review["overall_verdict"] == verdict
    assert review["items"][0]["verdict"] == verdict
    assert review["items"][0]["rule_id"] == "R5"


def test_not_matched_rule_emits_only_unverified_fallback():
    plan = _plan()
    plan["review_evidence"][0]["value"] = 0.99
    item = _generate(plan)["items"][0]
    assert item["verdict"] == "UNVERIFIED"
    assert item["rule_id"] is None


def test_partial_r5_evidence_is_insufficient():
    plan = _plan()
    plan["review_evidence"] = plan["review_evidence"][:2]
    item = _generate(plan)["items"][0]
    assert item["verdict"] == "INSUFFICIENT_EVIDENCE"
    assert item["missing_evidence"] == ["attribution_divergence"]


def test_null_r5_fact_is_insufficient_not_a_semantic_exception():
    plan = _plan()
    plan["review_evidence"][2]["value"] = None
    item = _generate(plan)["items"][0]
    assert item["verdict"] == "INSUFFICIENT_EVIDENCE"
    assert item["missing_evidence"] == ["attribution_divergence"]


def test_no_r5_evidence_is_unverified():
    plan = _plan()
    plan["review_evidence"] = []
    assert _generate(plan)["items"][0]["verdict"] == "UNVERIFIED"


def test_missing_runtime_confidence_state_is_insufficient():
    item = _generate(_plan(), confidence_states={})["items"][0]
    assert item["verdict"] == "INSUFFICIENT_EVIDENCE"
    assert item["runtime_confidence"] is None
    assert item["missing_rule_parameters"] == ["runtime_confidence_state"]


def test_authority_rejects_fabricated_insufficient_reason():
    plan = _plan()
    review = _generate(plan, confidence_states={})
    review["items"][0]["missing_rule_parameters"] = ["invented_blocker"]
    context = {"public_rule_context": [public_rule_from_card(load_rule_card("R5"))]}
    with pytest.raises(ContractValidationError, match="unsupported missing rule parameters"):
        validate_authoritative_review(plan, review, context)


def test_duplicate_r5_concept_is_rejected():
    plan = _plan()
    duplicate = copy.deepcopy(plan["review_evidence"][0])
    duplicate["fact_id"] = "review_fact_duplicate"
    plan["review_evidence"].append(duplicate)
    with pytest.raises(ContractValidationError, match="duplicate review concepts"):
        _generate(plan)


def test_mixed_r5_report_windows_are_rejected():
    plan = _plan()
    plan["review_evidence"][0]["period"] = "current_14_days"
    with pytest.raises(ContractValidationError, match="report window"):
        _generate(plan)


def test_irrelevant_duplicate_or_mixed_review_facts_are_rejected():
    plan = _plan()
    first = copy.deepcopy(plan["review_evidence"][0])
    first.update(fact_id="review_fact_irrelevant_1", name="irrelevant_metric")
    second = copy.deepcopy(first)
    second.update(fact_id="review_fact_irrelevant_2", period="current_14_days")
    plan["review_evidence"].extend([first, second])
    with pytest.raises(ContractValidationError, match="duplicate review concepts"):
        _generate(plan)


def test_irrelevant_mixed_review_windows_are_rejected_without_duplicates():
    plan = _plan()
    extra = copy.deepcopy(plan["review_evidence"][0])
    extra.update(
        fact_id="review_fact_irrelevant",
        name="irrelevant_metric",
        period="current_14_days",
    )
    plan["review_evidence"].append(extra)
    with pytest.raises(ContractValidationError, match="one report window"):
        _generate(plan)


def test_fact_entity_mismatch_is_rejected_before_review():
    plan = _plan()
    plan["review_evidence"][0]["entity_id"] = "another-channel"
    with pytest.raises(ContractValidationError, match="entity does not match"):
        _generate(plan)


def test_v1_rejects_rules_outside_the_approved_r5_scope():
    with pytest.raises(ContractValidationError, match="does not implement: R3"):
        _generate(_plan(), enabled_rule_ids=("R3", "R5"))


def test_duplicate_enabled_rule_is_rejected():
    with pytest.raises(ContractValidationError, match="must be unique"):
        _generate(_plan(), enabled_rule_ids=("R5", "R5"))


def test_non_finite_r5_fact_is_rejected():
    plan = _plan()
    plan["review_evidence"][0]["value"] = math.nan
    with pytest.raises(ContractValidationError, match="must be finite"):
        _generate(plan)


def test_authority_accepts_one_satisfied_any_branch(tmp_path):
    card = load_rule_card("R5")
    card["trigger_condition"] = {
        "any": [
            {"concept": "contribution_share", "op": "<", "ref": 0.10},
            {"concept": "spend_share", "op": ">", "ref": 0.20},
        ]
    }
    (tmp_path / "R5.json").write_text(json.dumps(card), encoding="utf-8")
    plan = _plan()
    plan["review_evidence"] = plan["review_evidence"][:1]
    review = _generate(_plan())
    review["items"][0]["matched_fact_ids"] = ["review_fact_001"]
    context = {"public_rule_context": [public_rule_from_card(card)]}
    validate_authoritative_review(plan, review, context, rules_dir=tmp_path)


def test_cli_reads_confidence_state_and_writes_review_atomically(tmp_path):
    output = tmp_path / "review.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "generate_ontology_review.py"),
            str(FIXTURE),
            str(output),
            "--confidence-state",
            str(STATE_FIXTURE),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(output.read_text(encoding="utf-8"))["overall_verdict"] == "CONFLICT"


def test_unusable_runtime_confidence_is_insufficient():
    state = {
        "schema_version": "1.0", "rule_id": "R5",
        "rule_version": "1.3-contract-hardening", "base_confidence": 0.62,
        "runtime_confidence": 0.59, "minimum_usable_confidence": 0.6,
        "validation_count": 0, "rejection_count": 0, "consecutive_bad_count": 0,
        "status": "ACTIVE", "processed_feedback_ids": [],
        "processed_feedback_digests": {}, "updated_at": "2026-08-03T12:00:00+08:00",
    }
    item = _generate(_plan(), confidence_states={"R5": state})["items"][0]
    assert item["verdict"] == "INSUFFICIENT_EVIDENCE"
    assert item["missing_rule_parameters"] == ["minimum_usable_confidence"]


def test_generation_is_deterministic_and_does_not_mutate_plan():
    plan = _plan()
    before = copy.deepcopy(plan)
    first = _generate(plan)
    second = _generate(plan)
    assert first == second
    assert plan == before
