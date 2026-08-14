from __future__ import annotations

import operator
import re
import math
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable

from app.domain.models import (
    ActionResult,
    Disposition,
    GuardrailEvaluation,
    Outcome,
    ReviewCreate,
    RuleEvaluation,
)
from app.errors import AppError
from app.ontology import OntologySnapshot


OPS: dict[str, Callable[[Any, Any], bool]] = {
    ">": operator.gt,
    ">=": operator.ge,
    "<": operator.lt,
    "<=": operator.le,
    "==": operator.eq,
    "!=": operator.ne,
}


@dataclass(frozen=True)
class EngineResult:
    outcome: Outcome
    disposition: Disposition
    reason: str
    matched_rules: list[str]
    winner_rule: str | None
    suppressed_rules: list[dict[str, str]]
    action: ActionResult | None
    rule_evaluations: list[RuleEvaluation]
    guardrail_evaluations: list[GuardrailEvaluation]


def _resolve_ref(ref: Any, baseline: float | None, concept: str) -> float | int | bool | str | Decimal:
    if isinstance(ref, (int, float, bool)):
        return ref
    match = re.fullmatch(r"baseline\*([0-9]+(?:\.[0-9]+)?)", str(ref))
    if match:
        if baseline is None:
            raise AppError(422, "MISSING_REQUIRED_METRIC", f"baseline is required for {concept}")
        return Decimal(str(baseline)) * Decimal(match.group(1))
    raise AppError(500, "UNSUPPORTED_RULE_EXPRESSION", "ontology contains an unsupported reference")


def _path_value(action: ActionResult, path: str) -> Any:
    value: Any = {"recommended_action": action.model_dump()}
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


class ReviewEngine:
    def __init__(self, ontology: OntologySnapshot):
        self.ontology = ontology

    def evaluate(self, request: ReviewCreate) -> EngineResult:
        if request.client_id not in self.ontology.clients:
            raise AppError(422, "UNKNOWN_CLIENT", f"unknown client_id: {request.client_id}")
        client = self.ontology.clients[request.client_id]
        if client["ontology_version_locked"] != self.ontology.version:
            raise AppError(409, "CLIENT_VERSION_MISMATCH", "client is locked to a different ontology version")
        if request.expected_ontology_version and request.expected_ontology_version != self.ontology.version:
            raise AppError(409, "ONTOLOGY_VERSION_MISMATCH", "requested ontology version is not loaded")

        unknown_rules = sorted(set(request.candidate_rules) - set(self.ontology.rules))
        if unknown_rules:
            raise AppError(422, "UNKNOWN_RULE", f"unknown rule IDs: {', '.join(unknown_rules)}")
        values = {item.concept: item for item in request.inputs}
        unknown_concepts = sorted(set(values) - set(self.ontology.concepts))
        if unknown_concepts:
            raise AppError(422, "UNKNOWN_CONCEPT", f"unknown concepts: {', '.join(unknown_concepts)}")

        for concept, item in values.items():
            bounds = self.ontology.concepts[concept].get("value_range")
            for label, candidate in (("value", item.value), ("baseline", item.baseline)):
                if candidate is None or isinstance(candidate, bool) or not isinstance(candidate, (int, float)):
                    continue
                if bounds:
                    lower, upper = bounds
                    if (lower is not None and candidate < lower) or (upper is not None and candidate > upper):
                        raise AppError(422, "METRIC_OUT_OF_RANGE", f"{concept} {label} is outside its canonical range")

        supplied = set(values)
        scope = list(request.candidate_rules)
        for rule_id, rule in self.ontology.rules.items():
            required = {item["concept"] for item in rule.get("match_inputs", []) if item.get("required", True)}
            if required and required.issubset(supplied) and rule_id not in scope:
                scope.append(rule_id)

        evaluations: list[RuleEvaluation] = []
        matched: list[str] = []
        retired_scoped = False
        for rule_id in scope:
            rule = self.ontology.rules[rule_id]
            expected_grain = rule["evaluation_grain"]["entity"]
            if rule_id in request.candidate_rules and request.entity.grain != expected_grain:
                raise AppError(422, "ENTITY_GRAIN_MISMATCH", f"{rule_id} requires entity grain {expected_grain}")
            if request.entity.grain != expected_grain:
                continue
            if rule["status"] != "ACTIVE":
                retired_scoped = True
                evaluations.append(RuleEvaluation(
                    rule_id=rule_id,
                    status=str(rule["status"]),
                    matched=False,
                    reason="Rule is retired and was not evaluated.",
                ))
                continue
            required = [item["concept"] for item in rule.get("match_inputs", []) if item.get("required", True)]
            missing = [concept for concept in required if concept not in values]
            if missing:
                raise AppError(
                    422,
                    "MISSING_REQUIRED_METRIC",
                    f"{rule_id} requires: {', '.join(missing)}",
                    details={"rule_id": rule_id, "missing": missing},
                )
            condition_results: list[dict[str, Any]] = []
            is_match = True
            for condition in rule["trigger_condition"].get("all", []):
                concept = condition["concept"]
                item = values[concept]
                if isinstance(item.value, bool) or not isinstance(item.value, (int, float)):
                    raise AppError(422, "INVALID_METRIC_TYPE", f"{concept} must be numeric for {rule_id}")
                right = _resolve_ref(condition["ref"], item.baseline, concept)
                try:
                    left_value = Decimal(str(item.value)) if isinstance(right, Decimal) else item.value
                    passed = OPS[condition["op"]](left_value, right)
                except TypeError as exc:
                    raise AppError(422, "INVALID_METRIC_TYPE", f"invalid value type for {concept}") from exc
                condition_results.append({
                    "concept": concept,
                    "value": item.value,
                    "operator": condition["op"],
                    "reference": right,
                    "passed": passed,
                })
                is_match = is_match and passed
            evaluations.append(RuleEvaluation(
                rule_id=rule_id,
                status=str(rule["status"]),
                matched=is_match,
                reason=rule["diagnosis"] if is_match else "Trigger condition was not satisfied.",
                conditions=condition_results,
            ))
            if is_match:
                matched.append(rule_id)

        winner: str | None = None
        suppressed: list[dict[str, str]] = []
        outcome = Outcome.NO_COVERAGE
        if set(matched) == {"R1", "R2"}:
            outcome = Outcome.CONFLICT
            winner = "R1"
            reason = "R1 and R2 conflict; R1 wins because its evidence is stronger and risk review has priority."
            suppressed.append({"rule_id": "R2", "by": "R1", "reason": "R1 evidence is stronger and risk review has priority"})
            for evaluation in evaluations:
                if evaluation.rule_id == "R2":
                    evaluation.suppressed_by = "R1"
        elif len(matched) == 1:
            outcome = Outcome.MATCH
            winner = matched[0]
            reason = str(self.ontology.rules[winner]["diagnosis"])
        elif len(matched) > 1:
            outcome = Outcome.CONFLICT
            reason = "Multiple active rules matched; no unapproved precedence was applied."
        elif retired_scoped:
            reason = "The requested rule is retired, so the current ontology does not cover this case."
        else:
            reason = "No active ontology rule matched the supplied inputs."

        action: ActionResult | None = None
        disposition = Disposition.NO_ACTION
        if winner:
            rule = self.ontology.rules[winner]
            action = ActionResult.model_validate(rule["recommended_action"])
            mode = rule.get("execution_policy", {}).get("mode")
            disposition = {
                "auto": Disposition.AUTO_EXECUTE,
                "review": Disposition.MANUAL_CONFIRM if winner == "R6" else Disposition.REVIEW,
                "manual": Disposition.MANUAL_CONFIRM,
            }.get(mode, Disposition.REVIEW)
        elif outcome == Outcome.CONFLICT:
            disposition = Disposition.REVIEW
        elif outcome == Outcome.NO_COVERAGE:
            disposition = Disposition.REVIEW if retired_scoped or not request.inputs else Disposition.NO_ACTION

        effective_action = action
        if request.proposed_action and winner:
            proposed = ActionResult.model_validate(request.proposed_action.model_dump())
            if proposed != action:
                outcome = Outcome.CONFLICT
                disposition = Disposition.REVIEW
                reason = f"{reason} The caller-proposed action differs from the Ontology recommendation."
        elif request.proposed_action and not winner:
            proposed = ActionResult.model_validate(request.proposed_action.model_dump())
            guarded_types = {
                action_type
                for guardrail in self.ontology.guardrails.values()
                for action_type in guardrail["applies_to_action_types"]
            }
            if proposed.type in guarded_types:
                action = proposed
                effective_action = proposed
                disposition = Disposition.AUTO_EXECUTE
            else:
                disposition = Disposition.REVIEW
        if winner == "R3" and action:
            pct = action.param.get("pct")
            limit = client["risk_tolerance"]["max_auto_budget_change_pct"] * 100
            if isinstance(pct, (int, float)) and pct > limit:
                disposition = Disposition.REVIEW

        guardrail_results = self._evaluate_guardrails(
            effective_action,
            values,
            review_on_incomplete={"G2"} if winner == "R3" else set(),
        )
        if any(result.applicable and result.passed is False for result in guardrail_results):
            disposition = Disposition.BLOCKED
        elif winner == "R3" and any(
            result.guardrail_id == "G2" and not result.applicable
            for result in guardrail_results
        ):
            # R3 remains a valid Ontology match, but cannot auto-execute until
            # the concrete daily-budget operands required by G2 are available.
            disposition = Disposition.REVIEW
        elif request.proposed_action and not winner and not any(result.applicable for result in guardrail_results):
            disposition = Disposition.REVIEW

        return EngineResult(
            outcome=outcome,
            disposition=disposition,
            reason=reason,
            matched_rules=matched,
            winner_rule=winner,
            suppressed_rules=suppressed,
            action=action,
            rule_evaluations=evaluations,
            guardrail_evaluations=guardrail_results,
        )

    def _evaluate_guardrails(
        self,
        action: ActionResult | None,
        values: dict[str, Any],
        *,
        review_on_incomplete: set[str] | None = None,
    ) -> list[GuardrailEvaluation]:
        review_on_incomplete = review_on_incomplete or set()
        results: list[GuardrailEvaluation] = []
        for guardrail_id, guardrail in self.ontology.guardrails.items():
            if action is None or action.type not in guardrail["applies_to_action_types"]:
                results.append(GuardrailEvaluation(guardrail_id=guardrail_id, applicable=False, passed=None))
                continue
            condition = guardrail["condition"]
            concept = condition["concept"]
            against = _path_value(action, condition["against"])
            if concept not in values and against is None:
                results.append(GuardrailEvaluation(
                    guardrail_id=guardrail_id,
                    applicable=False,
                    passed=None,
                    message="The action shape does not activate this guardrail contract.",
                ))
                continue
            if concept not in values or against is None:
                if guardrail_id in review_on_incomplete:
                    results.append(GuardrailEvaluation(
                        guardrail_id=guardrail_id,
                        applicable=False,
                        passed=None,
                        message="Concrete guardrail operands are unavailable; manual review is required.",
                    ))
                    continue
                raise AppError(422, "MISSING_REQUIRED_METRIC", f"{guardrail_id} requires {concept} and {condition['against']}")
            left = values[concept].value
            if (
                isinstance(left, bool)
                or isinstance(against, bool)
                or not isinstance(left, (int, float))
                or not isinstance(against, (int, float))
                or not math.isfinite(left)
                or not math.isfinite(against)
            ):
                raise AppError(422, "INVALID_METRIC_TYPE", f"{guardrail_id} operands must be finite numbers")
            try:
                passed = OPS[condition["op"]](left, against)
            except TypeError as exc:
                raise AppError(422, "INVALID_METRIC_TYPE", f"invalid guardrail value for {concept}") from exc
            results.append(GuardrailEvaluation(
                guardrail_id=guardrail_id,
                applicable=True,
                passed=passed,
                message=None if passed else str(guardrail["message"]),
            ))
        return results
