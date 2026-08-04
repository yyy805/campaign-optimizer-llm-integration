"""Exact but non-sensitive JSON Schema diagnostics for v7 Executor output."""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from jsonschema.exceptions import ValidationError

from .diagnostic_output_guard import DiagnosticOutputGuard, SafeOutputValidationFailure

SAFE_VALIDATORS = frozenset({"required", "type", "const", "enum", "additionalProperties", "oneOf", "minItems", "maxItems", "uniqueItems", "pattern", "maxLength"})
SAFE_FIELDS = frozenset({
    "schema_version", "workflow_version", "prompt_version", "knowledge_base_version",
    "status", "intent", "answer", "claims", "facts_used", "rule_ids_used",
    "plan_item_ids_used", "limitations_included", "retry_count", "fallback_used",
    "claim_id", "claim_type", "source_id", "field", "value",
})


class SchemaDiagnosticFailure(SafeOutputValidationFailure):
    def __init__(self, path: str, validator: str) -> None:
        super().__init__("SCHEMA", path)
        self.validator = validator


class DiagnosticOutputGuardV7(DiagnosticOutputGuard):
    def _schema_failure(self, error: ValidationError) -> SchemaDiagnosticFailure:
        validator = error.validator if error.validator in SAFE_VALIDATORS else "schema"
        parts: list[str | int] = list(error.absolute_path)
        if validator == "required" and isinstance(error.instance, dict) and isinstance(error.validator_value, Sequence):
            missing = next((name for name in error.validator_value if isinstance(name, str) and name in SAFE_FIELDS and name not in error.instance), None)
            if missing is not None:
                parts.append(missing)
        elif validator == "additionalProperties":
            parts.append("*")
        return SchemaDiagnosticFailure(_safe_path(parts), validator)

    def validate(self, raw_text: str, **kwargs: Any) -> dict[str, Any]:
        # Reuse v6 parsing/backend guards, but intercept its deliberately broad
        # schema category by validating the candidate schema first.
        import json
        from campaign_optimizer.contracts.validation import validate_contract_object

        try:
            output = json.loads(raw_text)
        except (TypeError, json.JSONDecodeError):
            return super().validate(raw_text, **kwargs)
        if isinstance(output, dict) and output.get("status") == "OK" and output.get("retry_count") == kwargs["retry_count"] and output.get("fallback_used") is False:
            try:
                validate_contract_object("llm_workflow_output", output)
            except ValidationError as error:
                raise self._schema_failure(error) from None
        return super().validate(raw_text, **kwargs)


def _safe_path(parts: Sequence[str | int]) -> str:
    path = "output"
    for part in parts:
        if isinstance(part, int) and part >= 0:
            path += f"[{part}]"
        elif part == "*":
            path += ".*"
        elif isinstance(part, str) and part in SAFE_FIELDS:
            path += f".{part}"
        else:
            path += ".field"
    return path
