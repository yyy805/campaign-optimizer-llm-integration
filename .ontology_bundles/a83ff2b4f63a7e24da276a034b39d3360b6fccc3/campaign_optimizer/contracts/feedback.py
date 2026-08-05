"""Deterministic feedback updates for runtime rule-confidence state."""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

from .validation import ContractValidationError, validate_contract_object

ELIGIBLE_VERDICTS = {"SUPPORT", "CONFLICT"}
UPDATABLE_STATUSES = {"ACTIVE", "PENDING_HUMAN_REVIEW"}


def _event_digest(event: dict[str, Any]) -> str:
    serialized = json.dumps(
        event, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def _validate_review_binding(
    event: dict[str, Any], review: dict[str, Any]
) -> None:
    if event["review_id"] != review["review_id"] or event["plan_id"] != review["plan_id"]:
        raise ContractValidationError("feedback does not match review_id/plan_id")
    review_item = next(
        (
            item
            for item in review["items"]
            if item["review_item_id"] == event["review_item_id"]
        ),
        None,
    )
    if review_item is None:
        raise ContractValidationError("feedback review_item_id does not exist")
    expected = {
        "plan_item_id": review_item["plan_item_id"],
        "rule_id": review_item["rule_id"],
        "rule_version": review_item["rule_version"],
        "verdict": review_item["verdict"],
    }
    actual = {key: event[key] for key in expected}
    if actual != expected:
        raise ContractValidationError("feedback payload does not match reviewed item snapshot")


def apply_feedback_event(
    state: dict[str, Any],
    event: dict[str, Any],
    review: dict[str, Any],
    policy: dict[str, Any],
) -> dict[str, Any]:
    """Return a new state; static rule cards are never mutated."""
    validate_contract_object("confidence_state", state)
    validate_contract_object("feedback_event", event)
    validate_contract_object("ontology_review", review)
    validate_contract_object("feedback_policy", policy)
    if policy["confidence_floor"] > policy["confidence_cap"]:
        raise ContractValidationError("confidence_floor cannot exceed confidence_cap")
    if state["status"] not in UPDATABLE_STATUSES:
        raise ContractValidationError(f"rule status {state['status']} cannot accept feedback")
    if event["rule_id"] != state["rule_id"] or event["rule_version"] != state["rule_version"]:
        raise ContractValidationError("feedback rule snapshot does not match confidence state")
    _validate_review_binding(event, review)

    digest = _event_digest(event)
    if event["feedback_id"] in state["processed_feedback_ids"]:
        stored = state["processed_feedback_digests"].get(event["feedback_id"])
        if stored != digest:
            raise ContractValidationError("duplicate feedback_id has a different payload")
        return deepcopy(state)

    updated = deepcopy(state)
    updated["processed_feedback_ids"].append(event["feedback_id"])
    updated["processed_feedback_digests"][event["feedback_id"]] = digest
    updated["updated_at"] = event["created_at"]

    if event["verdict"] not in ELIGIBLE_VERDICTS:
        validate_contract_object("confidence_state", updated)
        return updated

    rating = event["rating"]
    delta = policy[f"{rating.lower()}_delta"]
    updated["runtime_confidence"] = min(
        policy["confidence_cap"],
        max(policy["confidence_floor"], updated["runtime_confidence"] + delta),
    )
    if rating == "GOOD":
        updated["validation_count"] += 1
        updated["consecutive_bad_count"] = 0
    elif rating == "BAD":
        updated["rejection_count"] += 1
        updated["consecutive_bad_count"] += 1
    else:
        updated["consecutive_bad_count"] = 0

    if (
        updated["runtime_confidence"] < updated["minimum_usable_confidence"]
        or updated["rejection_count"] >= policy["bad_count_review_threshold"]
        or updated["consecutive_bad_count"]
        >= policy["consecutive_bad_review_threshold"]
    ):
        updated["status"] = "PENDING_HUMAN_REVIEW"

    validate_contract_object("confidence_state", updated)
    return updated
