"""Deterministic review-only ontology engine."""
from __future__ import annotations

import hashlib
import json
import math
from datetime import date
from pathlib import Path
from typing import Any

from campaign_optimizer.contracts.authority import (
    PERIOD_ALIASES,
    RULES_DIR,
    latest_rule_version,
    load_rule_card,
    public_rule_from_card,
    validate_authoritative_review,
)
from campaign_optimizer.contracts.concept_authority import validate_rule_fact_semantics
from campaign_optimizer.contracts.validation import (
    ContractValidationError,
    aggregate_verdict,
    validate_contract_object,
)

from .condition_evaluator import EvaluationStatus, evaluate_condition, required_concepts

DEFAULT_ENABLED_RULE_IDS: tuple[str, ...] = ()
IMPLEMENTED_RULE_IDS = frozenset({"R5"})


def _digest(value: Any, length: int = 16) -> str:
    canonical = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()[:length]


def _runtime_confidence(
    card: dict[str, Any], state: dict[str, Any] | None
) -> tuple[float, float | None, list[str]]:
    base = card.get("confidence")
    if (
        not isinstance(base, (int, float))
        or isinstance(base, bool)
        or not math.isfinite(base)
    ):
        return 0.0, None, ["base_confidence"]
    if state is None:
        return float(base), None, ["runtime_confidence_state"]
    else:
        validate_contract_object("confidence_state", state)
        version = latest_rule_version(card)
        if state["rule_id"] != card["rule_id"] or state["rule_version"] != version:
            raise ContractValidationError(
                f"confidence state does not match {card['rule_id']}@{version}"
            )
        if abs(state["base_confidence"] - base) > 1e-9:
            raise ContractValidationError(
                f"confidence state base does not match {card['rule_id']} card"
            )
        expected_minimum = card["confidence_model"]["thresholds"]["minimum_usable"]
        if abs(state["minimum_usable_confidence"] - expected_minimum) > 1e-9:
            raise ContractValidationError(
                f"confidence state minimum does not match {card['rule_id']} card"
            )
        runtime = float(state["runtime_confidence"])
        status = state["status"]
    minimum = card["confidence_model"]["thresholds"]["minimum_usable"]
    blockers: list[str] = []
    if not math.isfinite(runtime):
        blockers.append("finite_runtime_confidence")
        runtime = None
    if status != "ACTIVE":
        blockers.append("active_confidence_state")
    if runtime is not None and runtime < minimum:
        blockers.append("minimum_usable_confidence")
    return float(base), runtime, blockers


def _item_id(plan_item_id: str, rule_id: str | None, verdict: str) -> str:
    return f"review_item_{_digest([plan_item_id, rule_id, verdict], 12)}"


def _unverified(plan_item_id: str) -> dict[str, Any]:
    return {
        "review_item_id": _item_id(plan_item_id, None, "UNVERIFIED"),
        "plan_item_id": plan_item_id,
        "verdict": "UNVERIFIED",
        "rule_id": None,
        "rule_version": None,
        "base_confidence": None,
        "runtime_confidence": None,
        "matched_fact_ids": [],
        "missing_evidence": [],
        "missing_rule_parameters": [],
        "limitations": ["当前没有启用规则为该方案条目形成评价，可能是未覆盖或触发条件未命中。"],
    }


def _validate_candidate_facts(card: dict[str, Any], facts: list[dict[str, Any]]) -> None:
    concepts = required_concepts(card["trigger_condition"])
    relevant = [fact for fact in facts if fact["name"] in concepts]
    names = [fact["name"] for fact in relevant]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ContractValidationError(
            f"{card['rule_id']} has duplicate evidence concepts: {', '.join(duplicates)}"
        )
    allowed_periods = PERIOD_ALIASES.get(
        card["evaluation_grain"]["time"], {card["evaluation_grain"]["time"]}
    )
    periods = {fact["period"] for fact in relevant}
    if len(periods) > 1 or any(period not in allowed_periods for period in periods):
        raise ContractValidationError(
            f"{card['rule_id']} evidence must use one compatible report window"
        )
    errors = validate_rule_fact_semantics(
        card["trigger_condition"],
        [fact for fact in relevant if fact.get("value") is not None],
    )
    if errors:
        raise ContractValidationError("; ".join(errors))


def _validate_plan_integrity(plan: dict[str, Any]) -> None:
    try:
        start = date.fromisoformat(plan["period"]["start_date"])
        end = date.fromisoformat(plan["period"]["end_date"])
    except ValueError as exc:
        raise ContractValidationError("plan period contains an invalid date") from exc
    if (end - start).days != 13:
        raise ContractValidationError("next_14_days must contain exactly 14 calendar days")
    plan_items = {item["plan_item_id"]: item for item in plan["items"]}
    if len(plan_items) != len(plan["items"]):
        raise ContractValidationError("plan_item_id must be unique")
    facts = plan["decision_evidence"] + plan["review_evidence"]
    if len({fact["fact_id"] for fact in facts}) != len(facts):
        raise ContractValidationError("fact_id must be globally unique")
    for item in plan["items"]:
        current = item["current_budget"]
        recommended = item["recommended_budget"]
        delta = item["delta_pct"]
        if not all(math.isfinite(value) for value in (current, recommended, delta)):
            raise ContractValidationError(f"{item['plan_item_id']} budget values must be finite")
        action = item["action"]
        direction_ok = (
            action == "increase_budget" and delta > 0 and recommended > current
            or action == "decrease_budget" and delta < 0 and recommended < current
            or action == "keep_budget" and delta == 0 and recommended == current
        )
        if not direction_ok or abs(current * (1 + delta / 100) - recommended) > 0.01:
            raise ContractValidationError(
                f"{item['plan_item_id']} action and budget values are inconsistent"
            )
    for fact in facts:
        item = plan_items.get(fact["plan_item_id"])
        if item is None:
            raise ContractValidationError(f"{fact['fact_id']} references an unknown plan item")
        if (fact["entity_type"], fact["entity_id"]) != (
            item["entity_type"], item["entity_id"],
        ):
            raise ContractValidationError(
                f"{fact['fact_id']} entity does not match its plan item"
            )
    review_facts_by_item: dict[str, list[dict[str, Any]]] = {
        item_id: [] for item_id in plan_items
    }
    for fact in plan["review_evidence"]:
        review_facts_by_item[fact["plan_item_id"]].append(fact)
    for item_id, item_facts in review_facts_by_item.items():
        names = [fact["name"] for fact in item_facts]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ContractValidationError(
                f"{item_id} has duplicate review concepts: {', '.join(duplicates)}"
            )
        periods = {fact["period"] for fact in item_facts}
        if len(periods) > 1:
            raise ContractValidationError(
                f"{item_id} review evidence must use one report window"
            )


def generate_ontology_review(
    plan: dict[str, Any],
    *,
    ontology_version: str,
    confidence_state_version: str,
    release_identity: dict[str, str],
    confidence_states: dict[str, dict[str, Any]] | None = None,
    rules_dir: Path = RULES_DIR,
    enabled_rule_ids: tuple[str, ...] = DEFAULT_ENABLED_RULE_IDS,
) -> dict[str, Any]:
    """Generate a schema-valid review; pending rules are never enabled by default."""
    validate_contract_object("final_plan", plan)
    _validate_plan_integrity(plan)
    if release_identity.get("ontology_version") != ontology_version:
        raise ContractValidationError(
            "review ontology_version does not match release identity"
        )
    if len(set(enabled_rule_ids)) != len(enabled_rule_ids):
        raise ContractValidationError("enabled_rule_ids must be unique")
    unsupported = set(enabled_rule_ids) - IMPLEMENTED_RULE_IDS
    if unsupported:
        raise ContractValidationError(
            f"Review Engine v1 does not implement: {', '.join(sorted(unsupported))}"
        )
    states = confidence_states or {}
    unknown_states = set(states) - set(enabled_rule_ids)
    if unknown_states:
        raise ContractValidationError(
            f"confidence states contain disabled rules: {', '.join(sorted(unknown_states))}"
        )
    cards = [load_rule_card(rule_id, rules_dir) for rule_id in sorted(enabled_rule_ids)]
    for card in cards:
        if card["status"] != "ACTIVE":
            raise ContractValidationError(f"enabled rule {card['rule_id']} must be ACTIVE")

    facts_by_item: dict[str, list[dict[str, Any]]] = {
        item["plan_item_id"]: [] for item in plan["items"]
    }
    for fact in plan["review_evidence"]:
        if fact["plan_item_id"] not in facts_by_item:
            raise ContractValidationError(f"{fact['fact_id']} references an unknown plan item")
        facts_by_item[fact["plan_item_id"]].append(fact)

    review_items: list[dict[str, Any]] = []
    public_rules: dict[tuple[str, str], dict[str, Any]] = {}
    for plan_item in plan["items"]:
        generated: list[dict[str, Any]] = []
        item_facts = facts_by_item[plan_item["plan_item_id"]]
        for card in cards:
            if card["evaluation_grain"]["entity"] != plan_item["entity_type"]:
                continue
            concepts = required_concepts(card["trigger_condition"])
            relevant = [fact for fact in item_facts if fact["name"] in concepts]
            if not relevant:
                continue
            _validate_candidate_facts(card, item_facts)
            evaluation = evaluate_condition(
                card["trigger_condition"], {fact["name"]: fact for fact in relevant}
            )
            if evaluation.status == EvaluationStatus.NOT_MATCHED:
                continue

            version = latest_rule_version(card)
            public_rules[(card["rule_id"], version)] = public_rule_from_card(card)
            base, runtime, confidence_blockers = _runtime_confidence(
                card, states.get(card["rule_id"])
            )
            matched_ids = sorted(
                fact["fact_id"] for fact in relevant if fact.get("value") is not None
            )
            if evaluation.status == EvaluationStatus.INSUFFICIENT or confidence_blockers:
                verdict = "INSUFFICIENT_EVIDENCE"
                missing_evidence = sorted(evaluation.missing_evidence)
                missing_parameters = sorted(
                    set(evaluation.missing_rule_parameters).union(confidence_blockers)
                )
                if not missing_evidence and not missing_parameters:
                    missing_parameters = ["complete_rule_evaluation"]
            else:
                action = plan_item["action"]
                policy = card["review_policy"]
                verdict = (
                    "SUPPORT" if action in policy["supported_plan_actions"]
                    else "CONFLICT" if action in policy["conflicting_plan_actions"]
                    else policy["otherwise"]
                )
                missing_evidence = []
                missing_parameters = []
            generated.append({
                "review_item_id": _item_id(plan_item["plan_item_id"], card["rule_id"], verdict),
                "plan_item_id": plan_item["plan_item_id"],
                "verdict": verdict,
                "rule_id": card["rule_id"],
                "rule_version": version,
                "base_confidence": base,
                "runtime_confidence": runtime,
                "matched_fact_ids": matched_ids,
                "missing_evidence": missing_evidence,
                "missing_rule_parameters": missing_parameters,
                "limitations": list(card.get("known_limitations", [])),
            })
        review_items.extend(generated or [_unverified(plan_item["plan_item_id"])])

    seed = {
        "plan": plan,
        "ontology_version": ontology_version,
        "confidence_state_version": confidence_state_version,
        "release_identity": release_identity,
        "enabled_rule_ids": list(enabled_rule_ids),
        "confidence_states": states,
    }
    review = {
        "schema_version": "1.0",
        "review_id": f"review_{_digest(seed)}",
        "plan_id": plan["plan_id"],
        "source": "ONTOLOGY_ENGINE",
        "ontology_version": ontology_version,
        "release_identity": dict(release_identity),
        "confidence_state_version": confidence_state_version,
        "is_synthetic": plan["source"] == "DEMO_OPTIMIZER_STUB",
        "overall_verdict": aggregate_verdict(review_items),
        "items": review_items,
    }
    validate_contract_object("ontology_review", review)
    validate_authoritative_review(
        plan, review, {"public_rule_context": list(public_rules.values())},
        rules_dir=rules_dir,
    )
    return review
