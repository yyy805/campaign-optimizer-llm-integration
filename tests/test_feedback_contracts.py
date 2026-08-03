from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from campaign_optimizer.contracts.feedback import apply_feedback_event
from campaign_optimizer.contracts.validation import (
    ContractValidationError,
    validate_contract_object,
)

ROOT = Path(__file__).parent.parent


def _policy() -> dict:
    return json.loads(
        (ROOT / "campaign_optimizer/ontology/policies/feedback_policy.demo.json")
        .read_text(encoding="utf-8")
    )


def _state() -> dict:
    return {
        "schema_version": "1.0", "rule_id": "R1", "rule_version": "1.0",
        "base_confidence": 0.65, "runtime_confidence": 0.65,
        "minimum_usable_confidence": 0.5,
        "validation_count": 0, "rejection_count": 0,
        "consecutive_bad_count": 0, "status": "ACTIVE",
        "processed_feedback_ids": [], "processed_feedback_digests": {},
        "updated_at": "2026-08-03T00:00:00Z",
    }


def _event(rating: str = "GOOD", verdict: str = "SUPPORT", event_id: str = "fb-1") -> dict:
    return {
        "schema_version": "1.0", "feedback_id": event_id,
        "review_id": "review_1", "review_item_id": "review_item_1",
        "plan_id": "plan_1", "plan_item_id": "plan_item_1",
        "rule_id": "R1", "rule_version": "1.0", "verdict": verdict,
        "rating": rating, "actor_id": "user-1", "created_at": "2026-08-03T01:00:00Z",
    }


def _review(event: dict) -> dict:
    insufficient = event["verdict"] == "INSUFFICIENT_EVIDENCE"
    return {
        "schema_version": "1.0",
        "review_id": event["review_id"],
        "plan_id": event["plan_id"],
        "source": "DEMO_ONTOLOGY_STUB",
        "ontology_version": "test",
        "confidence_state_version": "test",
        "is_synthetic": True,
        "overall_verdict": event["verdict"],
        "items": [{
            "review_item_id": event["review_item_id"],
            "plan_item_id": event["plan_item_id"],
            "verdict": event["verdict"],
            "rule_id": event["rule_id"],
            "rule_version": event["rule_version"],
            "base_confidence": 0.65,
            "runtime_confidence": 0.65,
            "matched_fact_ids": [] if insufficient else ["review_fact_1"],
            "missing_evidence": ["required_signal"] if insufficient else [],
            "missing_rule_parameters": [],
            "limitations": [],
        }],
    }


def _apply(state: dict, event: dict) -> dict:
    return apply_feedback_event(state, event, _review(event), _policy())


def test_good_fine_bad_use_demo_policy_without_mutating_input():
    original = _state()
    good = _apply(original, _event("GOOD"))
    assert original["runtime_confidence"] == 0.65
    assert good["runtime_confidence"] == pytest.approx(0.67)
    fine = _apply(good, _event("FINE", event_id="fb-2"))
    assert fine["runtime_confidence"] == pytest.approx(0.67)
    bad = _apply(fine, _event("BAD", event_id="fb-3"))
    assert bad["runtime_confidence"] == pytest.approx(0.62)


def test_duplicate_event_is_idempotent():
    once = _apply(_state(), _event())
    twice = _apply(once, _event())
    assert twice == once


def test_insufficient_evidence_feedback_does_not_change_confidence():
    updated = _apply(_state(), _event("BAD", "INSUFFICIENT_EVIDENCE"))
    assert updated["runtime_confidence"] == 0.65
    assert updated["rejection_count"] == 0


def test_three_consecutive_bad_marks_rule_for_human_review():
    state = _state()
    for number in range(3):
        state = _apply(state, _event("BAD", event_id=f"fb-{number}"))
    assert state["status"] == "PENDING_HUMAN_REVIEW"


def test_feedback_cannot_update_another_rule_snapshot():
    event = _event()
    event["rule_version"] = "2.0"
    with pytest.raises(ContractValidationError):
        _apply(_state(), event)


def test_duplicate_id_with_changed_payload_is_rejected():
    once = _apply(_state(), _event())
    changed = _event("BAD")
    with pytest.raises(ContractValidationError, match="different payload"):
        _apply(once, changed)


def test_feedback_must_match_review_snapshot():
    event = _event()
    review = _review(event)
    review["items"][0]["verdict"] = "CONFLICT"
    review["overall_verdict"] = "CONFLICT"
    with pytest.raises(ContractValidationError, match="reviewed item snapshot"):
        apply_feedback_event(_state(), event, review, _policy())


def test_suspended_rule_rejects_feedback():
    state = _state()
    state["status"] = "SUSPENDED"
    with pytest.raises(ContractValidationError, match="cannot accept feedback"):
        _apply(state, _event())


def test_plan_decision_is_a_separate_accept_reject_contract():
    event = {
        "schema_version": "1.0", "decision_id": "decision-1", "plan_id": "plan-1",
        "plan_source_version": "1.0", "plan_hash": "a" * 64,
        "decision": "ACCEPT", "actor_id": "user-1", "created_at": "2026-08-03T01:00:00Z",
    }
    validate_contract_object("plan_decision_event", event)
    event["decision"] = "GOOD"
    with pytest.raises(jsonschema.ValidationError):
        validate_contract_object("plan_decision_event", event)
