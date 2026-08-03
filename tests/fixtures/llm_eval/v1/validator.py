"""Explicit validation for the synthetic v1 LLM evaluation datasets."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parent
SCENARIOS = {"routing_safety": "routing_eval", "generation": "generation_eval", "prompt_attacks": "prompt_attack_eval"}
DENIED_DATA_MARKERS = ("dashscope_api_key", "authorization: bearer", "customer_email", "production_account_id")


class DatasetValidationError(ValueError):
    """A stable, explicit v1 data-contract failure."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise DatasetValidationError(message)


def load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DatasetValidationError(f"cannot load JSON: {path.name}") from exc


def normalized_question(value: str) -> str:
    return " ".join(value.casefold().split())


def validate_all(root: Path = ROOT) -> int:
    root = Path(root).resolve()
    schema = load(root / "dataset.schema.json")
    try:
        Draft202012Validator.check_schema(schema)
    except Exception as exc:
        raise DatasetValidationError("dataset schema is invalid") from exc
    validator = Draft202012Validator(schema)
    index = load(root / "index.json")
    require(isinstance(index, dict), "index must be an object")
    require(set(index) == {"schema_version", "collection_id", "datasets"}, "index fields mismatch")
    require(index.get("schema_version") == "1.0", "index schema_version mismatch")
    require(index.get("collection_id") == "llm-eval-deidentified-v1", "index collection_id mismatch")
    entries = index.get("datasets")
    require(isinstance(entries, list) and entries, "index datasets must be non-empty")

    global_ids: set[str] = set()
    indexed_files: set[str] = set()
    indexed_types: set[str] = set()
    suite_ids: set[str] = set()
    questions: dict[str, list[dict[str, str]]] = defaultdict(list)
    families: dict[str, list[dict[str, str]]] = defaultdict(list)
    total = 0
    for entry in entries:
        require(isinstance(entry, dict), "index dataset entry must be an object")
        require(set(entry) == {"dataset_type", "file", "case_count"}, "index dataset entry fields mismatch")
        dataset_type, file_name = entry["dataset_type"], entry["file"]
        require(dataset_type in SCENARIOS, "index dataset_type is unsupported")
        require(isinstance(file_name, str) and Path(file_name).name == file_name, "index file must be a basename")
        require(file_name not in indexed_files, "index contains a duplicate file")
        require(dataset_type not in indexed_types, "index contains a duplicate dataset_type")
        require(isinstance(entry["case_count"], int) and entry["case_count"] > 0, "index case_count must be positive")
        indexed_files.add(file_name)
        indexed_types.add(dataset_type)
        payload = load(root / file_name)
        errors = sorted(validator.iter_errors(payload), key=lambda error: list(error.path))
        if errors:
            location = "/".join(str(part) for part in errors[0].path)
            raise DatasetValidationError(f"schema failure in {file_name} at {location}: {errors[0].message}")
        require(payload["dataset_type"] == dataset_type, f"index/payload type mismatch: {file_name}")
        require(payload["suite_id"] not in suite_ids, f"duplicate suite_id: {payload['suite_id']}")
        suite_ids.add(payload["suite_id"])
        cases = payload["cases"]
        require(len(cases) == entry["case_count"], f"index/payload count mismatch: {file_name}")
        serialized = json.dumps(payload, ensure_ascii=False).casefold()
        require(not any(marker in serialized for marker in DENIED_DATA_MARKERS), f"denied data marker in {file_name}")

        for case in cases:
            case_id = case["case_id"]
            require(case_id not in global_ids, f"duplicate global case_id: {case_id}")
            global_ids.add(case_id)
            require(case["scenario"] == SCENARIOS[dataset_type], f"scenario/type mismatch: {case_id}")
            record_family(case, families)
            if "question" in case:
                record_question(case["question"], case, questions)
            if dataset_type in {"routing_safety", "prompt_attacks"}:
                validate_routing_case(case)
        if dataset_type == "generation":
            validate_generation(root, payload, questions, families)
        total += len(cases)

    require(indexed_files == {"routing-safety.json", "generation.json", "prompt-attacks.json"}, "index dataset containment mismatch")
    validate_question_families(questions, families)
    return total


def record_family(item: dict[str, Any], families: dict[str, list[dict[str, str]]]) -> None:
    families[item["paraphrase_family_id"]].append({"split": item["split"], "intent": item.get("expected_intent", ""), "access": item.get("provider_access", "ALLOW")})


def record_question(question: str, item: dict[str, Any], questions: dict[str, list[dict[str, str]]]) -> None:
    questions[normalized_question(question)].append({"family": item["paraphrase_family_id"], "split": item["split"], "intent": item.get("expected_intent", ""), "access": item.get("provider_access", "ALLOW")})


def validate_routing_case(case: dict[str, Any]) -> None:
    expected_access = "ALLOW" if case["expected_provider_calls"] > 0 else "DENY"
    require(case["provider_access"] == expected_access, f"provider access/call mismatch: {case['case_id']}")
    source = case["route_expectation"]["source"]
    if source.startswith("classifier") and source != "classifier_unavailable":
        require("classifier_fixture" in case, f"classifier source lacks fixture: {case['case_id']}")
        fixture_type = case["classifier_fixture"]["fixture_type"]
        expected_fixture_type = {
            "classifier": "result",
            "classifier_low_confidence": "result",
            "classifier_failed": "error",
            "classifier_invalid": "invalid",
        }[source]
        require(fixture_type == expected_fixture_type, f"classifier source/fixture mismatch: {case['case_id']}")
    if case["expected_status"] == "REFUSED":
        require(case["expected_reason"] == case["expected_intent"], f"refusal reason mismatch: {case['case_id']}")
        require(case["canary_placement"] == "CONTROLLED_REQUEST_SUFFIX_NO_PROVIDER", f"canary placement mismatch: {case['case_id']}")
        require(case["canary"] not in case["question"], f"canary already contained in question: {case['case_id']}")
        simulated = {"output": {"status": "REFUSED", "intent": case["expected_intent"]}, "metadata": {"provider": "none", "provider_attempts": 0}}
        require(case["canary"] not in json.dumps(simulated), f"canary leaked in controlled simulation: {case['case_id']}")


def validate_generation(root: Path, payload: dict[str, Any], questions: dict[str, list[dict[str, str]]], families: dict[str, list[dict[str, str]]]) -> None:
    required = {"fixtures", "frozen_inputs", "common_executor_configs", "arm_configs", "candidate_fixtures", "measurement_contract", "quality_rubrics"}
    require(required.issubset(payload), "generation top-level refs are incomplete")
    fixtures_root = root.parents[1]
    for relative in payload["fixtures"].values():
        resolved = (root / relative).resolve()
        require(fixtures_root == resolved or fixtures_root in resolved.parents, "generation fixture escapes synthetic fixture root")
        require(resolved.is_file(), f"missing generation fixture: {relative}")
    expected_candidate_ids = {
        "candidate_clean_plan", "candidate_clean_review", "candidate_clean_rule",
        "candidate_semantic_guarantee_review", "candidate_numeric_error_review",
    }
    require(set(payload["candidate_fixtures"]) == expected_candidate_ids, "candidate fixture IDs incomplete")
    require({value["candidate_type"] for value in payload["candidate_fixtures"].values()} == {"clean", "semantic_guarantee_error", "subtle_numeric_error"}, "candidate types incomplete")
    for candidate_id, candidate in payload["candidate_fixtures"].items():
        candidate_path = (root / candidate["file"]).resolve()
        require(root in candidate_path.parents and candidate_path.is_file(), f"missing/escaping candidate fixture: {candidate_id}")
        load(candidate_path)
    baseline, reviewer = payload["arm_configs"].get("baseline_01", {}), payload["arm_configs"].get("reviewer_01", {})
    require(baseline.get("reviewer_enabled") is False and baseline.get("max_revision_rounds") == 0, "baseline arm config is not isolated")
    require(reviewer.get("reviewer_enabled") is True and reviewer.get("max_revision_rounds") == 1, "reviewer arm config is not isolated")
    require(payload["measurement_contract"] == {"cost_currency": "CNY", "latency_unit": "ms", "unexecuted_value": None}, "measurement contract mismatch")

    inputs, configs = payload["frozen_inputs"], payload["common_executor_configs"]
    rubrics, candidates = payload["quality_rubrics"], payload["candidate_fixtures"]
    for frozen in inputs.values():
        record_family(frozen, families)
        record_question(frozen["question"], frozen, questions)
    pair_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    referenced_candidates: set[str] = set()
    for case in payload["cases"]:
        require(case["frozen_input_id"] in inputs, f"missing frozen input ref: {case['case_id']}")
        require(case["common_executor_config_id"] in configs, f"missing executor config ref: {case['case_id']}")
        require(case["arm_config_id"] in payload["arm_configs"], f"missing arm config ref: {case['case_id']}")
        require(case["quality_rubric_id"] in rubrics, f"missing rubric ref: {case['case_id']}")
        require(case["candidate_id"] in candidates, f"missing candidate ref: {case['case_id']}")
        candidate = candidates[case["candidate_id"]]
        require(candidate["candidate_role"] == "paired", f"hard-gate-only candidate used in pair: {case['case_id']}")
        require(candidate["intent"] == case["expected_intent"], f"candidate/case intent mismatch: {case['case_id']}")
        referenced_candidates.add(case["candidate_id"])
        require(case["observed_cost_cny"] is None and case["observed_latency_ms"] is None, f"pilot measurements must be null: {case['case_id']}")
        frozen = inputs[case["frozen_input_id"]]
        require(case["split"] == frozen["split"] and case["paraphrase_family_id"] == frozen["paraphrase_family_id"], f"case/input containment mismatch: {case['case_id']}")
        require(case["expected_intent"] == frozen["expected_intent"], f"case/input intent mismatch: {case['case_id']}")
        expected_arm = "baseline_01" if case["comparison_arm"] == "baseline" else "reviewer_01"
        require(case["arm_config_id"] == expected_arm, f"comparison/arm mismatch: {case['case_id']}")
        expectation = case["arm_expectation"]
        require(case["expected_provider_calls"] == expectation["provider_calls"] and case["expected_status"] == expectation["status"], f"arm expectation/result mismatch: {case['case_id']}")
        if case["comparison_arm"] == "baseline":
            require(expectation == {"provider_calls":1,"revision_rounds":0,"status":"OK","reviewer_action":"NOT_APPLICABLE"}, f"baseline expectation mismatch: {case['case_id']}")
        elif candidate["candidate_type"] == "semantic_guarantee_error":
            require(expectation == {"provider_calls":3,"revision_rounds":1,"status":"OK","reviewer_action":"REVISE"}, f"semantic reviewer expectation mismatch: {case['case_id']}")
        else:
            require(expectation == {"provider_calls":2,"revision_rounds":0,"status":"OK","reviewer_action":"PASS"}, f"clean reviewer expectation mismatch: {case['case_id']}")
        pair_groups[case["pair_id"]].append(case)
    paired_candidate_ids = {candidate_id for candidate_id, candidate in candidates.items() if candidate["candidate_role"] == "paired"}
    require(referenced_candidates == paired_candidate_ids, "paired candidate containment mismatch")
    require(all(candidate_id not in referenced_candidates for candidate_id, candidate in candidates.items() if candidate["candidate_role"] == "hard_gate_only"), "hard-gate candidate leaked into pairs")
    for pair_id, pair in pair_groups.items():
        require(len(pair) == 2 and {case["comparison_arm"] for case in pair} == {"baseline", "reviewer_candidate"}, f"pair arms incomplete: {pair_id}")
        for field in ("frozen_input_id", "common_executor_config_id", "quality_rubric_id", "candidate_id", "split", "paraphrase_family_id"):
            require(len({case[field] for case in pair}) == 1, f"pair shared field mismatch: {pair_id}/{field}")


def validate_question_families(questions: dict[str, list[dict[str, str]]], families: dict[str, list[dict[str, str]]]) -> None:
    for normalized, records in questions.items():
        if len(records) > 1:
            require(len({record["family"] for record in records}) == 1, f"duplicate question family mismatch: {normalized[:40]}")
            require(len({record["split"] for record in records}) == 1, f"duplicate question split mismatch: {normalized[:40]}")
            require(len({(record["intent"], record["access"]) for record in records}) == 1, f"duplicate question label mismatch: {normalized[:40]}")
    for family_id, records in families.items():
        require(len({record["split"] for record in records}) == 1, f"family crosses splits: {family_id}")
        require(len({(record["intent"], record["access"]) for record in records}) == 1, f"family label containment mismatch: {family_id}")
