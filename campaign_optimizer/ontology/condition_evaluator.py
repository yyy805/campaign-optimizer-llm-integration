"""Deterministic, traceable evaluation of ontology rule conditions."""
from __future__ import annotations

import operator
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any


class EvaluationStatus(str, Enum):
    MATCHED = "MATCHED"
    NOT_MATCHED = "NOT_MATCHED"
    INSUFFICIENT = "INSUFFICIENT"


@dataclass(frozen=True)
class ConditionEvaluation:
    status: EvaluationStatus
    trace: tuple[dict[str, Any], ...]
    missing_evidence: tuple[str, ...] = ()
    missing_rule_parameters: tuple[str, ...] = ()


OPERATORS = {
    ">": operator.gt,
    ">=": operator.ge,
    "<": operator.lt,
    "<=": operator.le,
    "==": operator.eq,
    "!=": operator.ne,
}
BASELINE_REF = re.compile(r"^baseline(?:\*([0-9]+(?:\.[0-9]+)?))?$")


def required_concepts(condition: Any) -> set[str]:
    """Return every concept referenced by a condition tree."""
    if not isinstance(condition, dict):
        return set()
    concept = condition.get("concept")
    if isinstance(concept, str):
        return {concept}
    concepts: set[str] = set()
    for key in ("all", "any"):
        for child in condition.get(key, []):
            concepts.update(required_concepts(child))
    return concepts


def missing_required_concepts(condition: Any, available: set[str]) -> set[str]:
    """Return the smallest missing set while respecting all/any branch semantics."""
    if not isinstance(condition, dict):
        return set()
    concept = condition.get("concept")
    if isinstance(concept, str):
        return set() if concept in available else {concept}
    if isinstance(condition.get("all"), list):
        missing: set[str] = set()
        for child in condition["all"]:
            missing.update(missing_required_concepts(child, available))
        return missing
    if isinstance(condition.get("any"), list):
        candidates = [
            missing_required_concepts(child, available) for child in condition["any"]
        ]
        if not candidates or any(not candidate for candidate in candidates):
            return set()
        return min(candidates, key=lambda candidate: (len(candidate), sorted(candidate)))
    return set()


def _unique(values: list[str]) -> tuple[str, ...]:
    return tuple(sorted(set(values)))


def _leaf(condition: dict[str, Any], facts: dict[str, dict[str, Any]]) -> ConditionEvaluation:
    concept = condition.get("concept")
    fact = facts.get(concept)
    trace: dict[str, Any] = {
        "kind": "leaf", "concept": concept, "operator": condition.get("op"),
        "reference_expression": condition.get("ref"),
        "fact_id": fact.get("fact_id") if fact else None,
        "actual": fact.get("value") if fact else None,
        "reference": None, "status": EvaluationStatus.INSUFFICIENT.value,
    }
    if fact is None or fact.get("value") is None:
        return ConditionEvaluation(EvaluationStatus.INSUFFICIENT, (trace,), missing_evidence=(str(concept),))

    expression = condition.get("ref")
    reference = expression
    missing: list[str] = []
    if isinstance(expression, str):
        match = BASELINE_REF.fullmatch(expression)
        if match is None:
            missing.append(f"reference_{concept}")
        else:
            baseline = fact.get("baseline_value")
            if not isinstance(baseline, (int, float)) or isinstance(baseline, bool):
                missing.append(f"baseline_{concept}")
            else:
                factor = float(match.group(1)) if match.group(1) else 1.0
                reference = baseline * factor
    operation = OPERATORS.get(condition.get("op"))
    if operation is None:
        missing.append(f"operator_{concept}")
    if missing:
        return ConditionEvaluation(
            EvaluationStatus.INSUFFICIENT, (trace,),
            missing_rule_parameters=_unique(missing),
        )

    trace["reference"] = reference
    try:
        passed = bool(operation(fact["value"], reference))
    except (TypeError, ValueError):
        return ConditionEvaluation(
            EvaluationStatus.INSUFFICIENT, (trace,),
            missing_rule_parameters=(f"comparable_value_{concept}",),
        )
    status = EvaluationStatus.MATCHED if passed else EvaluationStatus.NOT_MATCHED
    trace["status"] = status.value
    return ConditionEvaluation(status, (trace,))


def evaluate_condition(
    condition: dict[str, Any], facts_by_concept: dict[str, dict[str, Any]]
) -> ConditionEvaluation:
    """Evaluate nested all/any conditions using conservative three-valued logic."""
    if "concept" in condition:
        return _leaf(condition, facts_by_concept)
    group = "all" if "all" in condition else "any" if "any" in condition else None
    children = condition.get(group, []) if group else []
    if not children:
        return ConditionEvaluation(
            EvaluationStatus.INSUFFICIENT,
            ({"kind": "group", "group": group, "status": "INSUFFICIENT"},),
            missing_rule_parameters=("trigger_condition",),
        )
    results = [evaluate_condition(child, facts_by_concept) for child in children]
    statuses = [result.status for result in results]
    if group == "all":
        status = (EvaluationStatus.NOT_MATCHED if EvaluationStatus.NOT_MATCHED in statuses
                  else EvaluationStatus.INSUFFICIENT if EvaluationStatus.INSUFFICIENT in statuses
                  else EvaluationStatus.MATCHED)
    else:
        status = (EvaluationStatus.MATCHED if EvaluationStatus.MATCHED in statuses
                  else EvaluationStatus.INSUFFICIENT if EvaluationStatus.INSUFFICIENT in statuses
                  else EvaluationStatus.NOT_MATCHED)
    return ConditionEvaluation(
        status,
        tuple(entry for result in results for entry in result.trace),
        _unique([name for result in results for name in result.missing_evidence]),
        _unique([name for result in results for name in result.missing_rule_parameters]),
    )
