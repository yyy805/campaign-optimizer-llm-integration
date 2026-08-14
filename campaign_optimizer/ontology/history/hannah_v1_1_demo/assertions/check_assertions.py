#!/usr/bin/env python3
"""Validate the ontology Demo assertion contract and semantic coverage."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft7Validator, FormatChecker

HERE = Path(__file__).resolve().parent
ONTOLOGY = HERE.parent
OPS = {
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def load_cards(folder: str, id_key: str) -> dict[str, dict[str, Any]]:
    return {card[id_key]: card for path in sorted((ONTOLOGY / folder).glob("*.json")) for card in [load_json(path)]}


def resolve_ref(condition: dict[str, Any], concept: dict[str, Any], supplied: dict[str, Any]) -> Any:
    ref = condition.get("ref")
    if ref is None:
        return concept.get("threshold")
    if isinstance(ref, str) and ref.startswith("baseline*"):
        baseline = supplied.get("baseline")
        if baseline is None:
            raise ValueError("baseline is required")
        return baseline * float(ref.split("*", 1)[1])
    return ref


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract-only", action="store_true")
    args = parser.parse_args()
    suite = load_json(HERE / "story_assertions.json")
    schema = load_json(HERE / "assertion.schema.json")
    rules = load_cards("rules", "rule_id")
    concepts = load_cards("concepts", "concept_id")
    clients = load_cards("clients", "client_id")
    guardrails = load_cards("guardrails", "guardrail_id")
    errors: list[str] = []

    schema_errors = sorted(Draft7Validator(schema, format_checker=FormatChecker()).iter_errors(suite), key=lambda e: list(e.path))
    for error in schema_errors:
        errors.append(f"schema {list(error.path)}: {error.message}")
    if schema_errors:
        print(f"FAIL (schema): {len(errors)} error(s)")
        for error in errors:
            print(f"- {error}")
        return 1

    seen: set[str] = set()
    assumption_ids = [item["id"] for item in suite.get("assumptions", [])]
    assumptions = set(assumption_ids)
    if len(assumption_ids) != len(assumptions):
        errors.append("assumptions: duplicate assumption id")
    scenarios = suite.get("scenarios", [])
    for scenario in scenarios:
        sid = scenario.get("assertion_id", "<missing>")
        if sid in seen:
            errors.append(f"{sid}: duplicate assertion_id")
        seen.add(sid)
        for rid in scenario.get("rule_refs", []):
            if rid not in rules:
                errors.append(f"{sid}: unknown rule {rid}")
        for gid in scenario.get("guardrail_refs", []):
            if gid not in guardrails:
                errors.append(f"{sid}: unknown guardrail {gid}")
        client_id = scenario.get("client_id")
        if client_id is not None and client_id not in clients:
            errors.append(f"{sid}: unknown client {client_id}")
        for aid in scenario.get("assumption_refs", []):
            if aid not in assumptions:
                errors.append(f"{sid}: unknown assumption {aid}")
        expected_block = scenario.get("expected", {})
        expected_rule_ids = list(expected_block.get("triggered_rules", []))
        if expected_block.get("winner") is not None:
            expected_rule_ids.append(expected_block["winner"])
        for suppressed in expected_block.get("suppressed", []):
            expected_rule_ids.extend([suppressed["rule_id"], suppressed["by"]])
        for rid in expected_rule_ids:
            if rid not in rules:
                errors.append(f"{sid}: unknown expected rule {rid}")
        expected_guardrail = expected_block.get("guardrail")
        if expected_guardrail is not None and expected_guardrail not in guardrails:
            errors.append(f"{sid}: unknown expected guardrail {expected_guardrail}")
        raw_inputs = scenario.get("inputs", [])
        inputs = {item.get("concept"): item for item in raw_inputs}
        if len(inputs) != len(raw_inputs):
            errors.append(f"{sid}: duplicate concept input")
        for item in scenario.get("inputs", []):
            cid = item.get("concept")
            if cid not in concepts:
                errors.append(f"{sid}: unknown concept {cid}")
                continue
            if item.get("source") == "demo_mock" and "mock_provenance" not in item:
                errors.append(f"{sid}: mock input {cid} lacks provenance")
            if item.get("source") != "demo_mock" and "mock_provenance" in item:
                errors.append(f"{sid}: non-mock input {cid} has mock provenance")
            value_range = concepts[cid].get("value_range")
            value = item.get("value")
            if isinstance(value_range, list) and len(value_range) == 2 and all(bound is None or isinstance(bound, (int, float)) for bound in value_range):
                lower, upper = value_range
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    if lower is not None and value < lower:
                        errors.append(f"{sid}: {cid} below value_range")
                    if upper is not None and value > upper:
                        errors.append(f"{sid}: {cid} above value_range")
            elif isinstance(value_range, list) and value_range and value_range != [None, None] and value not in value_range:
                errors.append(f"{sid}: {cid} value not in enum range")
        evaluated: dict[str, list[tuple[str, bool, Any, Any]]] = {}
        for rid in scenario.get("rule_refs", []):
            if rid not in rules:
                continue
            expected = rid in scenario.get("expected", {}).get("triggered_rules", [])
            conditions = rules[rid]["trigger_condition"]["all"]
            if scenario.get("category") in {"rule_positive", "rule_negative", "rule_boundary", "mock_model", "conflict"}:
                missing = [c["concept"] for c in conditions if c["concept"] not in inputs]
                if missing:
                    errors.append(f"{sid}: missing inputs for {rid}: {', '.join(missing)}")
                    continue
                actual_parts = []
                evaluated[rid] = []
                for condition in conditions:
                    supplied = inputs[condition["concept"]]
                    try:
                        ref = resolve_ref(condition, concepts[condition["concept"]], supplied)
                        if ref is None:
                            raise ValueError("reference resolves to null")
                        result = OPS[condition["op"]](supplied["value"], ref)
                        actual_parts.append(result)
                        evaluated[rid].append((condition["concept"], result, supplied["value"], ref))
                    except (TypeError, ValueError) as exc:
                        errors.append(f"{sid}: cannot evaluate {rid}/{condition['concept']}: {exc}")
                if len(actual_parts) == len(conditions) and all(actual_parts) != expected:
                    errors.append(f"{sid}: evaluated {rid}={all(actual_parts)} but expected {expected}")
            if expected:
                action = scenario.get("expected", {}).get("action")
                if scenario.get("category") not in {"conflict", "coverage"} and action and action != rules[rid]["recommended_action"]:
                    errors.append(f"{sid}: action differs from {rid} card")
                if scenario.get("category") in {"rule_positive", "mock_model"}:
                    if action is None or not scenario.get("expected", {}).get("diagnosis_contains"):
                        errors.append(f"{sid}: triggered rule requires action and diagnosis assertion")
                if scenario["entity"]["grain"] != rules[rid]["evaluation_grain"]["entity"]:
                    errors.append(f"{sid}: entity grain differs from {rid} card")

    if not args.contract_only:
        rule_ids = {rid for rid, rule in rules.items() if rule.get("status") == "ACTIVE"}
        referenced = {rid for s in scenarios for rid in s.get("rule_refs", [])}
        active_referenced = {rid for rid in referenced if rules[rid].get("status") == "ACTIVE"}
        if active_referenced != rule_ids:
            errors.append(f"coverage: active rule refs expected {sorted(rule_ids)}, got {sorted(active_referenced)}")
        retired_ids = {rid for rid, rule in rules.items() if rule.get("status") == "RETIRED"}
        for rid in sorted(retired_ids):
            rule = rules[rid]
            retired_cases = [
                s for s in scenarios
                if s.get("rule_refs") == [rid]
                and s.get("category") == "coverage"
                and s.get("expected", {}).get("coverage_status") == "NO_COVERAGE"
            ]
            if len(retired_cases) != 1:
                errors.append(f"coverage: retired {rid} needs exactly one dedicated NO_COVERAGE scenario")
            elif retired_cases[0].get("expected", {}).get("triggered_rules") != []:
                errors.append(f"{retired_cases[0]['assertion_id']}: retired rule must not trigger")
            if rule.get("trigger_condition", {}).get("all") != [] or rule.get("match_inputs", []) != []:
                errors.append(f"coverage: retired {rid} must have no executable conditions or inputs")
            if rule.get("recommended_action") != {"type": "no_action", "param": {}}:
                errors.append(f"coverage: retired {rid} must not recommend an executable action")
        for rid in sorted(rule_ids):
            rule_scenarios = [s for s in scenarios if rid in s.get("rule_refs", [])]
            for category in ("rule_positive", "rule_negative", "rule_boundary"):
                if not any(s["category"] == category for s in rule_scenarios):
                    errors.append(f"coverage: {rid} missing {category}")
            condition_count = len(rules[rid]["trigger_condition"]["all"])
            negatives = [s for s in rule_scenarios if s["category"] == "rule_negative"]
            if len(negatives) < condition_count:
                errors.append(f"coverage: {rid} needs {condition_count} condition-negative scenarios")
            failed_concepts: set[str] = set()
            for negative in negatives:
                supplied = {item["concept"]: item for item in negative["inputs"]}
                false_now = []
                for condition in rules[rid]["trigger_condition"]["all"]:
                    try:
                        ref = resolve_ref(condition, concepts[condition["concept"]], supplied[condition["concept"]])
                        if not OPS[condition["op"]](supplied[condition["concept"]]["value"], ref):
                            false_now.append(condition["concept"])
                    except (KeyError, TypeError, ValueError):
                        pass
                if len(false_now) != 1:
                    errors.append(f"{negative['assertion_id']}: negative must break exactly one condition")
                failed_concepts.update(false_now)
            expected_concepts = {condition["concept"] for condition in rules[rid]["trigger_condition"]["all"]}
            if failed_concepts != expected_concepts:
                errors.append(f"coverage: {rid} negative cases do not isolate every condition")
            boundaries = [s for s in rule_scenarios if s["category"] == "rule_boundary"]
            for boundary in boundaries:
                supplied = {item["concept"]: item for item in boundary["inputs"]}
                equals = []
                for condition in rules[rid]["trigger_condition"]["all"]:
                    if condition["concept"] not in supplied:
                        continue
                    try:
                        ref = resolve_ref(condition, concepts[condition["concept"]], supplied[condition["concept"]])
                        equals.append(supplied[condition["concept"]]["value"] == ref)
                    except (TypeError, ValueError):
                        pass
                if not any(equals):
                    errors.append(f"{boundary['assertion_id']}: boundary has no value equal to a resolved reference")
        for gid in guardrails:
            cases = [s for s in scenarios if gid in s.get("guardrail_refs", [])]
            dispositions = {s["expected"]["disposition"] for s in cases}
            if not {"AUTO_EXECUTE", "BLOCKED"}.issubset(dispositions):
                errors.append(f"coverage: {gid} needs pass and blocked cases")
            card = guardrails[gid]
            for case in cases:
                action = case["expected"].get("action", {})
                if action.get("type") not in card["applies_to_action_types"]:
                    errors.append(f"{case['assertion_id']}: action type not covered by {gid}")
                    continue
                param_name = card["condition"]["against"].rsplit(".", 1)[-1]
                if param_name not in action.get("param", {}):
                    errors.append(f"{case['assertion_id']}: missing guardrail action parameter {param_name}")
                    continue
                concept_input = next((item for item in case["inputs"] if item["concept"] == card["condition"]["concept"]), None)
                if concept_input is None:
                    errors.append(f"{case['assertion_id']}: missing guardrail concept input")
                    continue
                passes = OPS[card["condition"]["op"]](concept_input["value"], action["param"][param_name])
                expected_disposition = "AUTO_EXECUTE" if passes else "BLOCKED"
                if case["expected"]["disposition"] != expected_disposition:
                    errors.append(f"{case['assertion_id']}: guardrail disposition inconsistent")
                if not passes and case["expected"].get("message") != card["message"]:
                    errors.append(f"{case['assertion_id']}: guardrail message differs from card")
        client_cases = {s.get("client_id"): s["expected"]["disposition"] for s in scenarios if s["category"] == "client_governance"}
        if client_cases != {"demo_client_001": "AUTO_EXECUTE", "demo_client_002": "REVIEW"}:
            errors.append(f"coverage: client governance outcomes incorrect: {client_cases}")
        for case in [s for s in scenarios if s["category"] == "client_governance"]:
            pct = case["expected"].get("action", {}).get("param", {}).get("pct")
            limit = clients[case["client_id"]]["risk_tolerance"]["max_auto_budget_change_pct"] * 100
            calculated = "AUTO_EXECUTE" if pct is not None and pct <= limit else "REVIEW"
            if case["expected"]["disposition"] != calculated:
                errors.append(f"{case['assertion_id']}: client policy disposition inconsistent")
        statuses = {s["expected"].get("coverage_status") for s in scenarios if s["category"] == "coverage"}
        if statuses != {"MATCH", "CONFLICT", "NO_COVERAGE"}:
            errors.append(f"coverage: missing coverage states: {statuses}")
        if len([s for s in scenarios if s["category"] == "fast_forward"]) != 2:
            errors.append("coverage: exactly two fast-forward branches required")
        conflict = [s for s in scenarios if s["category"] == "conflict"]
        if not any(s["expected"].get("winner") == "R1" and any(x["rule_id"] == "R2" and x["by"] == "R1" for x in s["expected"].get("suppressed", [])) for s in conflict):
            errors.append("coverage: R1/R2 conflict winner and suppression missing")
        for case in conflict:
            if set(case["rule_refs"]) != {"R1", "R2"} or set(case["expected"]["triggered_rules"]) != {"R1", "R2"}:
                errors.append(f"{case['assertion_id']}: conflict must contain triggered R1 and R2")
        lifecycle = [s for s in scenarios if s["category"] == "lifecycle"]
        if not any(s.get("transition", {}).get("before", {}).get("rejection_count") == 9 and s.get("transition", {}).get("after") == {"rejection_count": 10, "status": "PENDING_HUMAN_REVIEW"} for s in lifecycle):
            errors.append("coverage: R6 tenth-rejection transition missing")
        for case in lifecycle:
            transition = case.get("transition", {})
            if case.get("rule_refs") != ["R6"] or transition.get("event") != "reject_recommendation" or transition.get("before", {}).get("status") != "ACTIVE":
                errors.append(f"{case['assertion_id']}: lifecycle is not the R6 rejection transition")
        flows = [s for s in scenarios if s["category"] == "fast_forward"]
        events = {s.get("transition", {}).get("event") for s in flows}
        if events != {"accept_then_advance_14_days", "reject_then_advance_14_days"}:
            errors.append("coverage: fast-forward accept/reject events incorrect")

    mode = "contract" if args.contract_only else "complete"
    if errors:
        print(f"FAIL ({mode}): {len(errors)} error(s)")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"PASS ({mode}): {len(scenarios)} scenario(s), {len(rules)} rules, {len(guardrails)} guardrails, {len(clients)} clients")
    return 0


if __name__ == "__main__":
    sys.exit(main())
