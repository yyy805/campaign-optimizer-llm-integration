#!/usr/bin/env python3
"""Validate live MTA CSV headers and ontology mapping coverage."""

from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft7Validator

HERE = Path(__file__).resolve().parent
ONTOLOGY = HERE.parent
PROJECT_ROOT = next(parent for parent in HERE.parents if (parent / "docs").is_dir())
ALLOWED_OPS = {">", ">=", "<", "<=", "==", "!="}
NO_SOURCE_STRATEGIES = {"demo_mock", "owned_outside_mta"}
EXPECTED_TRANSFORMS = {
    "acos": "sum(cost) / sum(sales)",
    "ctr": "sum(clicks) / sum(impressions)",
    "impressions_growth": "(current_7d_impressions - prior_7d_impressions) / prior_7d_impressions",
    "roas": "sum(sales) / sum(cost)",
    "cvr": "sum(purchases) / sum(clicks)",
}
EXTERNAL_OWNERS: dict[str, tuple[str, str]] = {}


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def load_cards(folder: str, key: str) -> dict[str, dict[str, Any]]:
    return {
        card[key]: card
        for path in sorted((ONTOLOGY / folder).glob("*.json"))
        for card in [load_json(path)]
    }


def csv_header(path: Path) -> list[str]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        row = next(csv.reader(handle), None)
    if row is None:
        raise ValueError("empty CSV")
    return [field.strip() for field in row]


def formula_tokens(formula: str) -> set[str]:
    return set(re.findall(r"[a-z][a-z0-9_]*", formula))


def main() -> int:
    mapping = load_json(HERE / "field_mapping.json")
    schema = load_json(HERE / "field_mapping.schema.json")
    concepts = load_cards("concepts", "concept_id")
    rules = {rid: rule for rid, rule in load_cards("rules", "rule_id").items() if rule["status"] == "ACTIVE"}
    errors: list[str] = []

    for error in sorted(Draft7Validator(schema).iter_errors(mapping), key=lambda item: list(item.path)):
        errors.append(f"schema {list(error.path)}: {error.message}")

    datasets = {item["dataset_id"]: item for item in mapping.get("datasets", [])}
    if len(datasets) != len(mapping.get("datasets", [])):
        errors.append("datasets: duplicate dataset_id")
    live_headers: dict[str, set[str]] = {}
    for dataset_id, dataset in datasets.items():
        path = PROJECT_ROOT / dataset["path"]
        if not path.is_file():
            errors.append(f"{dataset_id}: missing source file {dataset['path']}")
            continue
        try:
            actual = csv_header(path)
        except (OSError, ValueError) as exc:
            errors.append(f"{dataset_id}: cannot read header: {exc}")
            continue
        expected = dataset["expected_headers"]
        missing = [name for name in expected if name not in actual]
        unexpected = [name for name in actual if name not in expected]
        if missing or unexpected or actual != expected:
            errors.append(
                f"{dataset_id}: header drift; missing={missing}, unexpected={unexpected}, "
                f"order_matches={actual == expected}"
            )
        live_headers[dataset_id] = set(actual)

    seen: set[str] = set()
    concept_strategies: dict[str, list[dict[str, Any]]] = {}
    for item in mapping.get("mappings", []):
        mid = item["mapping_id"]
        if mid in seen:
            errors.append(f"{mid}: duplicate mapping_id")
        seen.add(mid)
        concept_id = item.get("concept_id")
        etl_id = item.get("etl_identifier")
        if (concept_id is None) == (etl_id is None):
            errors.append(f"{mid}: exactly one of concept_id or etl_identifier is required")
        if concept_id is not None:
            if concept_id not in concepts:
                errors.append(f"{mid}: unknown concept {concept_id}")
            concept_strategies.setdefault(concept_id, []).append(item)
        source_id = item.get("source_dataset")
        strategy = item.get("strategy")
        if strategy in NO_SOURCE_STRATEGIES:
            if source_id is not None or item.get("source_fields"):
                errors.append(f"{mid}: {strategy} must not claim MTA source fields")
        else:
            if source_id not in datasets:
                errors.append(f"{mid}: unknown or missing source dataset {source_id}")
            elif source_id in live_headers:
                unknown = sorted(set(item.get("source_fields", [])) - live_headers[source_id])
                if unknown:
                    errors.append(f"{mid}: missing live headers in {source_id}: {unknown}")
        if strategy == "unsupported_grain":
            if item.get("status") != "blocked" or not item.get("blocker") or not item.get("workaround"):
                errors.append(f"{mid}: unsupported grain requires blocked status, blocker, and workaround")
            if item.get("source_grain") == item.get("target_grain"):
                errors.append(f"{mid}: unsupported grain cannot claim identical grains")
        if strategy in {"demo_mock", "owned_outside_mta"} and item.get("ownership") == "mta_team":
            errors.append(f"{mid}: mock/outside strategy cannot default ownership to MTA")
        if concept_id in EXTERNAL_OWNERS:
            expected_owner, expected_strategy = EXTERNAL_OWNERS[concept_id]
            if (item.get("ownership"), strategy) != (expected_owner, expected_strategy):
                errors.append(
                    f"{mid}: {concept_id} must remain {expected_owner}/{expected_strategy} "
                    "until an explicit ownership agreement changes the contract"
                )
        if concept_id in EXPECTED_TRANSFORMS and EXPECTED_TRANSFORMS[concept_id] not in item.get("transform", ""):
            errors.append(f"{mid}: transform does not preserve the approved {concept_id} aggregation formula")
        for rid in item.get("supported_rules", []):
            if rid not in rules:
                errors.append(f"{mid}: unknown supported rule {rid}")

    required_inputs: dict[str, set[str]] = {}
    for rid, rule in rules.items():
        conditions = rule.get("trigger_condition", {}).get("all", [])
        required_inputs[rid] = {condition.get("concept") for condition in conditions}
        for condition in conditions:
            if condition.get("op") not in ALLOWED_OPS:
                errors.append(f"{rid}: unsupported rule operator {condition.get('op')}")
        for concept_id in required_inputs[rid]:
            strategies = concept_strategies.get(concept_id, [])
            if not strategies:
                errors.append(f"coverage: {rid}/{concept_id} has no source strategy")
            elif not any(rid in item.get("supported_rules", []) for item in strategies):
                errors.append(f"coverage: {rid}/{concept_id} mapping does not declare rule support")
            for item in [entry for entry in strategies if rid in entry.get("supported_rules", [])]:
                status = item.get("status")
                if status in {"reusable", "derived"}:
                    entity = rule.get("evaluation_grain", {}).get("entity")
                    if entity and entity not in item.get("target_grain", ""):
                        errors.append(
                            f"coverage: {rid}/{concept_id} claims {status} but target grain "
                            f"does not include rule entity {entity}"
                        )
                if item.get("strategy") == "unsupported_grain" and status != "blocked":
                    errors.append(f"coverage: {rid}/{concept_id} grain gap must remain visibly blocked")

    # Derived ontology formulas must have source strategies for their dependencies,
    # unless the concept is explicitly blocked or external.
    for concept_id, items in concept_strategies.items():
        if concept_id not in concepts or not concepts[concept_id].get("formula"):
            continue
        if all(item["strategy"] not in {"unsupported_grain", "owned_outside_mta", "demo_mock"} for item in items):
            for token in formula_tokens(concepts[concept_id]["formula"]):
                if token in concepts and token not in concept_strategies:
                    errors.append(f"formula: {concept_id} dependency {token} has no mapping")

    r5_mappings = {
        item["mapping_id"]: item
        for item in mapping.get("mappings", [])
        if "R5" in item.get("supported_rules", []) and item.get("concept_id") is not None
    }
    required_r5 = {"FM-R5-CONTRIBUTION", "FM-R5-SPEND-SHARE", "FM-R5-DIVERGENCE"}
    if set(r5_mappings) != required_r5:
        errors.append(f"R5: expected mappings {sorted(required_r5)}, got {sorted(r5_mappings)}")
    else:
        for mid, item in r5_mappings.items():
            transform = item.get("transform", "")
            if "same batch and report window" not in transform:
                errors.append(f"{mid}: R5 inputs must enforce the same batch and report window")
        for mid in ("FM-R5-CONTRIBUTION", "FM-R5-DIVERGENCE"):
            if "outcome == revenue" not in r5_mappings[mid].get("transform", ""):
                errors.append(f"{mid}: R5 must deterministically select the revenue outcome")
        denominator = next(
            (item for item in mapping.get("mappings", []) if item["mapping_id"] == "FM-R5-TOTAL-SPEND"),
            None,
        )
        if denominator is None or "zero" not in denominator.get("null_behavior", ""):
            errors.append("FM-R5-TOTAL-SPEND: zero denominator must return NO_COVERAGE")

    request_ids = [item["request_id"] for item in mapping.get("requests", [])]
    if len(request_ids) != len(set(request_ids)):
        errors.append("requests: duplicate request_id")
    categories = {item["category"] for item in mapping.get("requests", [])}
    if categories != {"must_have", "optional", "outside_mta"}:
        errors.append(f"requests: expected all request categories, got {sorted(categories)}")

    if errors:
        print(f"FAIL: {len(errors)} error(s)")
        for error in errors:
            print(f"- {error}")
        return 1
    status_counts = {
        status: sum(1 for item in mapping["mappings"] if item["status"] == status)
        for status in {item["status"] for item in mapping["mappings"]}
    }
    print(
        f"PASS: {len(datasets)} datasets, {len(mapping['mappings'])} source strategies "
        f"{dict(sorted(status_counts.items()))}, {len(rules)} rules, {len(mapping['requests'])} requests"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
