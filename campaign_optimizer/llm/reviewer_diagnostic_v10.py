"""Non-sensitive Reviewer schema diagnostics for v10."""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Mapping

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from .agent_workflow_v5 import SCHEMAS, _read_schema

SAFE_REVIEWER_FIELDS = frozenset({
    "schema_version", "candidate_id", "packet_digest", "decision",
    "violation_codes", "evidence_source_ids", "revision_actions",
    "operation", "target_claim_id", "source_id",
})
SAFE_REVIEWER_VALIDATORS = frozenset({
    "required", "type", "const", "enum", "additionalProperties", "minItems",
    "maxItems", "uniqueItems", "pattern",
})


class ReviewerDecisionFailure(ValueError):
    def __init__(self, category: str, validator: str, path: str) -> None:
        self.category = category
        self.validator = validator
        self.path = path
        super().__init__("reviewer decision rejected")


def validate_reviewer_schema(value: Mapping[str, Any]) -> None:
    try:
        Draft202012Validator(_read_schema("reviewer_decision_v3.schema.json")).validate(dict(value))
    except ValidationError as error:
        validator = error.validator if error.validator in SAFE_REVIEWER_VALIDATORS else "schema"
        parts: list[str | int] = list(error.absolute_path)
        if validator == "required" and isinstance(error.instance, dict) and isinstance(error.validator_value, Sequence):
            missing = next((name for name in error.validator_value if isinstance(name, str) and name in SAFE_REVIEWER_FIELDS and name not in error.instance), None)
            if missing is not None:
                parts.append(missing)
        elif validator == "additionalProperties":
            parts.append("*")
        raise ReviewerDecisionFailure("SCHEMA", validator, _safe_reviewer_path(parts)) from None


def _safe_reviewer_path(parts: Sequence[str | int]) -> str:
    path = "reviewer"
    for part in parts:
        if isinstance(part, int) and part >= 0:
            path += f"[{part}]"
        elif part == "*":
            path += ".*"
        elif isinstance(part, str) and part in SAFE_REVIEWER_FIELDS:
            path += f".{part}"
        else:
            path += ".field"
    return path
