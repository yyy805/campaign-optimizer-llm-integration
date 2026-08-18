"""Explicit validation for the frozen reviewer judgment v1 dataset."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parent
DECISIONS = ("PASS", "REVISE", "REJECT")
LABEL_CLASSES = (
    "explain_only",
    "refuse_assertion",
    "pending_review_semantics",
    "safety",
)
VIOLATION_CODES = (
    "UNSUPPORTED_CLAIM",
    "UNSUPPORTED_GUARANTEE",
    "MISSING_LIMITATION",
    "ID_OR_VALUE_MISMATCH",
    "INTENT_MISMATCH",
    "NEW_FACT",
    "SAFETY_VIOLATION",
    "UNRESOLVABLE_CONFLICT",
)
IDENTITY_FIELDS = (
    "ontology_version",
    "rule_version",
    "engine_version",
    "schema_version",
    "source_commit",
    "package_checksum",
)
CANDIDATE_REQUIRED_FIELDS = (
    "schema_version",
    "workflow_version",
    "prompt_version",
    "knowledge_base_version",
    "status",
    "intent",
    "answer",
    "claims",
    "facts_used",
    "rule_ids_used",
    "plan_item_ids_used",
    "limitations_included",
    "retry_count",
    "fallback_used",
)
DENIED_DATA_MARKERS = (
    "dashscope_api_key",
    "authorization: bearer",
    "customer_email",
    "production_account_id",
)


class DatasetValidationError(ValueError):
    """A stable, explicit reviewer-judgment data-contract failure."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise DatasetValidationError(message)


def load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DatasetValidationError(f"cannot load JSON: {path.name}") from exc


def validate_candidate_structure(candidate: dict[str, Any], case_id: str) -> None:
    require(set(candidate) == set(CANDIDATE_REQUIRED_FIELDS), f"candidate fields mismatch: {case_id}")
    require(candidate["workflow_version"] == "not-published", f"candidate workflow_version must stay pinned: {case_id}")
    require(candidate["prompt_version"] == "draft-1.0", f"candidate prompt_version must stay pinned: {case_id}")
    require(candidate["knowledge_base_version"] == "not-published", f"candidate knowledge_base_version must stay pinned: {case_id}")
    require(candidate["status"] == "OK", f"candidate must stay OK status: {case_id}")
    require(candidate["intent"] == "EXPLAIN_REVIEW", f"candidate intent mismatch: {case_id}")
    require(candidate["retry_count"] == 0, f"candidate retry_count mismatch: {case_id}")
    require(candidate["fallback_used"] is False, f"candidate fallback_used mismatch: {case_id}")
    require(candidate["limitations_included"] is True, f"candidate must disclose limitations: {case_id}")
    require(candidate["rule_ids_used"] == [], f"pending context cannot use rules: {case_id}")
    claim_ids = set()
    for claim in candidate["claims"]:
        claim_id = claim["claim_id"]
        require(claim_id not in claim_ids, f"duplicate claim_id in {case_id}: {claim_id}")
        claim_ids.add(claim_id)
        require(
            claim["claim_type"] != "RULE_FIELD",
            f"RULE_FIELD claims are structurally impossible in pending context: {case_id}/{claim_id}",
        )
        if claim["claim_type"] == "REVIEW_FIELD" and claim["field"] == "verdict":
            require(claim["value"] == "UNVERIFIED", f"candidate verdict claim must stay UNVERIFIED: {case_id}/{claim_id}")


def validate_label_consistency(case: dict[str, Any]) -> None:
    case_id = case["case_id"]
    decision = case["expected_decision"]
    codes = case["acceptable_violation_codes"]
    label_class = case["label_class"]
    if decision == "PASS":
        require(codes == [], f"PASS case must carry no violation codes: {case_id}")
        require(case["revision_required"] is False, f"PASS case must not require revision: {case_id}")
    elif decision == "REVISE":
        require(len(codes) > 0, f"REVISE case must list acceptable codes: {case_id}")
        require(case["revision_required"] is True, f"REVISE case must require revision: {case_id}")
    else:
        require(codes == ["SAFETY_VIOLATION"], f"REJECT case must pin SAFETY_VIOLATION: {case_id}")
        require(case["revision_required"] is False, f"REJECT case must not require revision: {case_id}")
    if label_class == "explain_only":
        require(decision == "PASS", f"explain_only case must expect PASS: {case_id}")
    elif label_class == "refuse_assertion":
        require(decision == "REVISE", f"refuse_assertion case must expect REVISE: {case_id}")
    elif label_class == "safety":
        require(decision == "REJECT", f"safety case must expect REJECT: {case_id}")


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
    require(
        set(index) == {"schema_version", "collection_id", "datasets", "plan_fixture", "review_fixture"},
        "index fields mismatch",
    )
    require(index["schema_version"] == "1.0", "index schema_version mismatch")
    require(index["collection_id"] == "reviewer-judgment-v1", "index collection_id mismatch")

    fixtures_root = root.parents[1].resolve()
    plan_path = (root / index["plan_fixture"]).resolve()
    require(fixtures_root in plan_path.parents, "plan fixture escapes synthetic fixture root")
    require(plan_path.is_file(), f"missing plan fixture: {index['plan_fixture']}")
    review_path = (root / index["review_fixture"]).resolve()
    require(root in review_path.parents and review_path.is_file(), "missing/escaping review fixture")
    review = load(review_path)
    require(review.get("overall_verdict") == "UNVERIFIED", "review fixture must stay UNVERIFIED")
    require(set(review.get("release_identity", {})) == set(IDENTITY_FIELDS), "review release identity fields mismatch")
    require(review.get("ontology_version") == review["release_identity"]["ontology_version"], "review ontology_version mismatch")

    entries = index["datasets"]
    require(isinstance(entries, list) and len(entries) == 1, "index must list exactly one dataset")
    entry = entries[0]
    require(set(entry) == {"dataset_type", "file", "case_count"}, "index dataset entry fields mismatch")
    require(entry["dataset_type"] == "reviewer_judgment", "index dataset_type mismatch")
    require(isinstance(entry["case_count"], int) and entry["case_count"] > 0, "index case_count must be positive")

    payload = load(root / entry["file"])
    errors = sorted(validator.iter_errors(payload), key=lambda error: list(error.path))
    if errors:
        location = "/".join(str(part) for part in errors[0].path)
        raise DatasetValidationError(f"schema failure in {entry['file']} at {location}: {errors[0].message}")

    cases = payload["cases"]
    require(len(cases) == entry["case_count"], f"index/payload count mismatch: {entry['file']}")
    seen_ids: set[str] = set()
    seen_candidates: set[str] = set()
    decisions: set[str] = set()
    for case in cases:
        case_id = case["case_id"]
        require(case_id not in seen_ids, f"duplicate case_id: {case_id}")
        seen_ids.add(case_id)
        candidate_file = case["candidate_file"]
        require(candidate_file not in seen_candidates, f"candidate reused across cases: {candidate_file}")
        seen_candidates.add(candidate_file)
        candidate_path = (root / candidate_file).resolve()
        require(root in candidate_path.parents and candidate_path.is_file(), f"missing/escaping candidate: {case_id}")
        validate_candidate_structure(load(candidate_path), case_id)
        validate_label_consistency(case)
        decisions.add(case["expected_decision"])
    require(decisions == set(DECISIONS), "dataset must cover PASS, REVISE and REJECT")
    require(
        {case["label_class"] for case in cases} == set(LABEL_CLASSES),
        "dataset must cover every label class",
    )

    for path in sorted(root.rglob("*.json")):
        serialized = path.read_text(encoding="utf-8").casefold()
        require(
            not any(marker in serialized for marker in DENIED_DATA_MARKERS),
            f"denied data marker in {path.relative_to(root)}",
        )
    return len(cases)


if __name__ == "__main__":
    print(validate_all())
