"""Batch-check concept cards, references, and rule concept dependencies."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from jsonschema import Draft7Validator

DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(DEFAULT_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(DEFAULT_PROJECT_ROOT))

from campaign_optimizer.contracts.concept_authority import collect_rule_concepts


def validate_ontology_package(project_root: Path) -> list[str]:
    ontology = project_root / "campaign_optimizer" / "ontology"
    concepts_dir = ontology / "concepts"
    errors: list[str] = []
    try:
        manifest = json.loads(
            (ontology / "asset_manifest.json").read_text(encoding="utf-8")
        )
        schema = json.loads(
            (ontology / "schemas" / "concept.schema.json").read_text(encoding="utf-8")
        )
        rule_schema = json.loads(
            (ontology / "schemas" / "rule.schema.json").read_text(encoding="utf-8")
        )
        guardrail_schema = json.loads(
            (ontology / "schemas" / "guardrail.schema.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        return [f"cannot load ontology manifest or schema: {exc}"]

    if set(manifest) != {"manifest_version", "concepts", "rules"}:
        errors.append("ontology asset manifest has an invalid shape")
        expected_concepts: set[str] = set()
        expected_rules: set[str] = set()
    else:
        expected_concepts = set(manifest["concepts"])
        expected_rules = set(manifest["rules"])
        if manifest["manifest_version"] != "1.0":
            errors.append("ontology asset manifest_version must be 1.0")
        if len(expected_concepts) != len(manifest["concepts"]):
            errors.append("ontology asset manifest contains duplicate concepts")
        if len(expected_rules) != len(manifest["rules"]):
            errors.append("ontology asset manifest contains duplicate rules")
    actual_concepts = {path.stem for path in concepts_dir.glob("*.json")}
    actual_rules = {path.stem for path in (ontology / "rules").glob("R*.json")}
    if actual_concepts != expected_concepts:
        errors.append(
            "concept asset set differs from manifest: "
            f"missing={sorted(expected_concepts - actual_concepts)}, "
            f"extra={sorted(actual_concepts - expected_concepts)}"
        )
    if actual_rules != expected_rules:
        errors.append(
            "rule asset set differs from manifest: "
            f"missing={sorted(expected_rules - actual_rules)}, "
            f"extra={sorted(actual_rules - expected_rules)}"
        )
    validator = Draft7Validator(schema)
    rule_validator = Draft7Validator(rule_schema)
    guardrail_validator = Draft7Validator(guardrail_schema)

    cards: dict[str, dict] = {}
    for path in sorted(concepts_dir.glob("*.json")):
        try:
            card = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{path.name}: invalid JSON: {exc}")
            continue
        cards[card.get("concept_id", path.stem)] = card
        if card.get("concept_id") != path.stem:
            errors.append(f"{path.name} concept_id must match its filename")
        for error in validator.iter_errors(card):
            location = ".".join(str(part) for part in error.path) or "<root>"
            errors.append(f"{path.name}:{location}: {error.message}")

    known = set(cards)
    for concept_id, card in cards.items():
        for relation in card.get("related_concepts", []):
            if relation["target"] not in known:
                errors.append(
                    f"{concept_id} references missing concept {relation['target']}"
                )

    for path in sorted((ontology / "rules").glob("R*.json")):
        try:
            rule = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{path.name}: invalid JSON: {exc}")
            continue
        for error in rule_validator.iter_errors(rule):
            location = ".".join(str(part) for part in error.path) or "<root>"
            errors.append(f"{path.name}:{location}: {error.message}")
        if rule.get("rule_id") != path.stem:
            errors.append(f"{path.name} rule_id must match its filename")
        trigger_concepts = collect_rule_concepts(rule.get("trigger_condition", {}))
        missing = trigger_concepts - known
        if missing:
            errors.append(f"{path.name} references missing concepts: {', '.join(sorted(missing))}")
        match_inputs = rule.get("match_inputs", [])
        input_concept_list = [item.get("concept") for item in match_inputs]
        input_concepts = set(input_concept_list)
        if len(input_concepts) != len(input_concept_list):
            errors.append(f"{path.name} match_inputs cannot contain duplicate concepts")
        if any(item.get("required") is not True for item in match_inputs):
            errors.append(f"{path.name} trigger match_inputs must all be required")
        if input_concepts != trigger_concepts:
            errors.append(f"{path.name} match_inputs must equal trigger concepts")
        if rule.get("status") == "ACTIVE" and not trigger_concepts:
            errors.append(f"{path.name} active rule must have a non-empty trigger")
        policy = rule.get("review_policy", {})
        supported = set(policy.get("supported_plan_actions", []))
        conflicting = set(policy.get("conflicting_plan_actions", []))
        if supported.intersection(conflicting):
            errors.append(f"{path.name} supports and conflicts with the same action")
        thresholds = rule.get("confidence_model", {}).get("thresholds", {})
        if thresholds.get("high", 0) < thresholds.get("minimum_usable", 0):
            errors.append(f"{path.name} high confidence must not be below minimum usable")
        if rule.get("status") == "RETIRED" and (supported or conflicting):
            errors.append(f"{path.name} retired rule cannot issue decisive review verdicts")
    for path in sorted((ontology / "guardrails").glob("G*.json")):
        try:
            guardrail = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{path.name}: invalid JSON: {exc}")
            continue
        for error in guardrail_validator.iter_errors(guardrail):
            location = ".".join(str(part) for part in error.path) or "<root>"
            errors.append(f"{path.name}:{location}: {error.message}")
        if guardrail.get("guardrail_id") != path.stem:
            errors.append(f"{path.name} guardrail_id must match its filename")
        condition = guardrail.get("condition")
        if isinstance(condition, dict) and condition.get("concept") not in known:
            errors.append(f"{path.name} references a missing concept")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root")
    args = parser.parse_args()
    root = args.project_root or os.getenv("CAMPAIGN_OPTIMIZER_PROJECT_ROOT")
    project_root = Path(root).expanduser().resolve() if root else DEFAULT_PROJECT_ROOT
    errors = validate_ontology_package(project_root)
    if errors:
        print("Ontology package check failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Ontology package check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
