from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import uuid4

from app.domain.plan_review_models import FinalPlan, OntologyReview, OntologyReviewItem
from app.errors import AppError
from app.ontology import OntologySnapshot
from app.services.review_engine import OPS, _resolve_ref


GRAIN_MAP = {"channel": "touchpoint", "touchpoint": "touchpoint", "campaign": "campaign", "account": "platform"}
ACTION_POLICY = {
    "R1": ({}, {"increase_budget"}),
    "R2": ({"keep_budget"}, set()),
    "R3": ({"increase_budget"}, {"decrease_budget"}),
    "R4": (set(), {"increase_budget", "keep_budget"}),
    "R5": ({"decrease_budget"}, {"increase_budget"}),
    "R6": (set(), {"increase_budget"}),
}
VERDICT_PRIORITY = {"SUPPORT": 0, "NOT_APPLICABLE": 1, "UNVERIFIED": 2, "INSUFFICIENT_EVIDENCE": 3, "CONFLICT": 4}
PERIOD_ALIASES = {
    "snapshot": {"snapshot", "current_snapshot"},
    "7day": {"7day", "current_7_days"},
    "14day": {"14day", "current_14_days"},
}


class PlanReviewService:
    def __init__(self, ontology: OntologySnapshot):
        self.ontology = ontology

    def evaluate(self, plan: FinalPlan) -> OntologyReview:
        items: list[OntologyReviewItem] = []
        review_id = f"review_{uuid4().hex}"
        for plan_item in plan.items:
            facts = [fact for fact in plan.review_evidence if fact.plan_item_id == plan_item.plan_item_id]
            facts_by_name = {fact.name: fact for fact in facts}
            if len(facts_by_name) != len(facts):
                raise AppError(422, "DUPLICATE_REVIEW_FACT", "review_evidence repeats a concept for one plan item")
            unknown = sorted(set(facts_by_name) - set(self.ontology.concepts))
            if unknown:
                raise AppError(422, "UNKNOWN_CONCEPT", f"unknown review evidence concepts: {', '.join(unknown)}")
            canonical_grain = GRAIN_MAP[plan_item.entity_type]
            for fact in facts:
                concept = self.ontology.concepts[fact.name]
                if concept["unit"] != fact.unit:
                    raise AppError(422, "UNIT_MISMATCH", f"{fact.name} requires unit {concept['unit']}")
                if concept["granularity"]["entity"] != canonical_grain:
                    raise AppError(422, "ENTITY_GRAIN_MISMATCH", f"{fact.name} does not apply to {canonical_grain}")
            produced: list[OntologyReviewItem] = []
            for rule_id, rule in self.ontology.rules.items():
                if rule["status"] != "ACTIVE" or rule["evaluation_grain"]["entity"] != canonical_grain:
                    continue
                if rule_id not in ACTION_POLICY:
                    raise AppError(
                        503,
                        "RULE_POLICY_UNAVAILABLE",
                        f"active rule {rule_id} has no approved plan-action policy",
                        retryable=False,
                    )
                required = [entry["concept"] for entry in rule.get("match_inputs", []) if entry.get("required", True)]
                supplied = [name for name in required if name in facts_by_name and facts_by_name[name].value is not None]
                missing = [name for name in required if name not in supplied]
                if self._known_condition_is_false(rule_id, rule, facts_by_name):
                    continue
                missing_parameters = [
                    f"{condition['concept']}_baseline"
                    for condition in rule["trigger_condition"].get("all", [])
                    if isinstance(condition.get("ref"), str)
                    and condition["concept"] in facts_by_name
                    and facts_by_name[condition["concept"]].value is not None
                    and facts_by_name[condition["concept"]].baseline_value is None
                ]
                if missing or missing_parameters:
                    produced.append(self._item(
                        review_id, plan_item.plan_item_id, rule_id, "INSUFFICIENT_EVIDENCE",
                        [facts_by_name[name].fact_id for name in supplied], missing, missing_parameters,
                    ))
                    continue
                if not self._matches(rule_id, rule, facts_by_name):
                    continue
                supported, conflicting = ACTION_POLICY[rule_id]
                verdict = "SUPPORT" if plan_item.action in supported else "CONFLICT" if plan_item.action in conflicting else "NOT_APPLICABLE"
                produced.append(self._item(
                    review_id, plan_item.plan_item_id, rule_id, verdict,
                    [facts_by_name[name].fact_id for name in required], [], [],
                ))
            if not produced:
                produced.append(OntologyReviewItem(
                    review_item_id=f"review_item_{uuid4().hex}", plan_item_id=plan_item.plan_item_id,
                    verdict="UNVERIFIED", rule_id=None, rule_version=None, base_confidence=None,
                    runtime_confidence=None, matched_fact_ids=[], missing_evidence=[],
                    missing_rule_parameters=[], limitations=["No active canonical rule covered this plan item."],
                ))
            items.extend(produced)
        overall = max((item.verdict for item in items), key=VERDICT_PRIORITY.__getitem__)
        return OntologyReview(
            review_id=review_id, plan_id=plan.plan_id, ontology_version=self.ontology.version,
            confidence_state_version=self.ontology.version,
            is_synthetic=plan.source == "DEMO_OPTIMIZER_STUB", overall_verdict=overall, items=items,
        )

    def _item(self, review_id: str, plan_item_id: str, rule_id: str, verdict: str,
              matched: list[str], missing: list[str], missing_parameters: list[str]) -> OntologyReviewItem:
        rule = self.ontology.rules[rule_id]
        return OntologyReviewItem(
            review_item_id=f"review_item_{uuid4().hex}", plan_item_id=plan_item_id, verdict=verdict,
            rule_id=rule_id, rule_version=self.ontology.version, base_confidence=rule["confidence"],
            runtime_confidence=rule["confidence"], matched_fact_ids=matched,
            missing_evidence=missing, missing_rule_parameters=missing_parameters,
            limitations=list(rule.get("known_limitations") or []),
        )

    def _matches(self, rule_id: str, rule: Any, facts: dict[str, Any]) -> bool:
        for condition in rule["trigger_condition"].get("all", []):
            fact = facts[condition["concept"]]
            self._validate_fact_for_rule(rule_id, rule, fact)
            if isinstance(fact.value, bool) or not isinstance(fact.value, (int, float)):
                raise AppError(422, "INVALID_METRIC_TYPE", f"{fact.name} must be numeric for {rule_id}")
            right = _resolve_ref(condition["ref"], fact.baseline_value, fact.name)
            left = Decimal(str(fact.value)) if isinstance(right, Decimal) else fact.value
            if not OPS[condition["op"]](left, right):
                return False
        return True

    def _known_condition_is_false(self, rule_id: str, rule: Any, facts: dict[str, Any]) -> bool:
        for condition in rule["trigger_condition"].get("all", []):
            fact = facts.get(condition["concept"])
            if fact is None or fact.value is None:
                continue
            if isinstance(condition.get("ref"), str) and fact.baseline_value is None:
                continue
            self._validate_fact_for_rule(rule_id, rule, fact)
            if isinstance(fact.value, bool) or not isinstance(fact.value, (int, float)):
                raise AppError(422, "INVALID_METRIC_TYPE", f"{fact.name} must be numeric for {rule_id}")
            right = _resolve_ref(condition["ref"], fact.baseline_value, fact.name)
            left = Decimal(str(fact.value)) if isinstance(right, Decimal) else fact.value
            if not OPS[condition["op"]](left, right):
                return True
        return False

    def _validate_fact_for_rule(self, rule_id: str, rule: Any, fact: Any) -> None:
        rule_time = rule["evaluation_grain"]["time"]
        if fact.period not in PERIOD_ALIASES.get(rule_time, {rule_time}):
            raise AppError(422, "PERIOD_MISMATCH", f"{fact.name} period does not match {rule_id}")
        if fact.baseline_value is not None and fact.baseline_period not in PERIOD_ALIASES.get(rule_time, {rule_time}) | {"baseline", "account_baseline"}:
            raise AppError(422, "BASELINE_PERIOD_MISMATCH", f"{fact.name} baseline period does not match {rule_id}")
        bounds = self.ontology.concepts[fact.name].get("value_range")
        for label, value in (("value", fact.value), ("baseline", fact.baseline_value)):
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise AppError(422, "INVALID_METRIC_TYPE", f"{fact.name} {label} must be numeric for {rule_id}")
            if bounds and ((bounds[0] is not None and value < bounds[0]) or (bounds[1] is not None and value > bounds[1])):
                raise AppError(422, "METRIC_OUT_OF_RANGE", f"{fact.name} {label} is outside its canonical range")
