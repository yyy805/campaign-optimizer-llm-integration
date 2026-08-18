"""Concept-card loading and rule-fact semantic validation."""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

CONCEPTS_DIR = Path(__file__).parent.parent / "ontology" / "concepts"


def collect_rule_concepts(value: Any) -> set[str]:
    concepts: set[str] = set()
    if isinstance(value, dict):
        concept = value.get("concept")
        if isinstance(concept, str):
            concepts.add(concept)
        for child in value.values():
            concepts.update(collect_rule_concepts(child))
    elif isinstance(value, list):
        for child in value:
            concepts.update(collect_rule_concepts(child))
    return concepts


def load_concept_card(
    concept_id: str, concepts_dir: Path = CONCEPTS_DIR
) -> dict[str, Any]:
    path = concepts_dir / f"{concept_id}.json"
    if not path.is_file():
        raise ValueError(f"authoritative concept library has no {concept_id}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read concept {concept_id}: {exc}") from exc


def validate_rule_fact_semantics(
    trigger_condition: dict[str, Any],
    matched_facts: list[dict[str, Any]],
    *,
    concepts_dir: Path = CONCEPTS_DIR,
) -> list[str]:
    """Validate declared type, unit, and domain without recalculating formulas."""
    errors: list[str] = []
    rule_concepts = collect_rule_concepts(trigger_condition)
    for fact in matched_facts:
        concept_id = fact["name"]
        if concept_id not in rule_concepts:
            continue
        try:
            card = load_concept_card(concept_id, concepts_dir)
        except ValueError as exc:
            errors.append(str(exc))
            continue

        if fact["unit"] != card["unit"]:
            errors.append(
                f"{fact['fact_id']}单位{fact['unit']}与概念{concept_id}的{card['unit']}不一致"
            )

        value = fact["value"]
        value_type = card.get("value_type", "number")
        if value_type == "number":
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                errors.append(f"{fact['fact_id']}必须是概念{concept_id}的数值")
                continue
            if not math.isfinite(value):
                errors.append(f"{fact['fact_id']} must be finite for concept {concept_id}")
                continue
            lower, upper = card["value_range"]
            if lower is not None and value < lower:
                errors.append(f"{fact['fact_id']}低于概念{concept_id}下限{lower}")
            if upper is not None and value > upper:
                errors.append(f"{fact['fact_id']}高于概念{concept_id}上限{upper}")
        elif value_type == "string_enum":
            allowed = card.get("allowed_values") or []
            if not isinstance(value, str) or value not in allowed:
                errors.append(f"{fact['fact_id']}必须是概念{concept_id}允许值{allowed}之一")
        elif value_type == "boolean":
            if not isinstance(value, bool):
                errors.append(f"{fact['fact_id']} must be boolean for 概念{concept_id}")
        else:
            errors.append(f"unsupported value_type {value_type} for {concept_id}")
    return errors
