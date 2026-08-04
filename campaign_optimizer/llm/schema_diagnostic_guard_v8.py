"""Schema-derived, allowlisted enum repair metadata for v8."""
from __future__ import annotations

import json
from pathlib import Path

from .schema_diagnostic_guard_v7 import DiagnosticOutputGuardV7, SchemaDiagnosticFailure

LEGAL_CLAIM_TYPES = (
    "PLAN_FIELD",
    "PLAN_PERIOD_FIELD",
    "REVIEW_FIELD",
    "FACT_VALUE",
    "RULE_FIELD",
)


def _schema_claim_types() -> tuple[str, ...]:
    schema_path = Path(__file__).parent.parent / "schemas" / "llm_workflow_output.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    values = tuple(schema["definitions"]["claim"]["properties"]["claim_type"]["enum"])
    if len(values) != len(set(values)) or set(values) != set(LEGAL_CLAIM_TYPES):
        raise ValueError("claim_type schema enum is outside the approved allowlist")
    return values


CLAIM_TYPE_ALLOWED_VALUES = _schema_claim_types()


class SchemaDiagnosticFailureV8(SchemaDiagnosticFailure):
    def __init__(self, path: str, validator: str, allowed_values: tuple[str, ...] = ()) -> None:
        super().__init__(path, validator)
        self.allowed_values = allowed_values


class DiagnosticOutputGuardV8(DiagnosticOutputGuardV7):
    def _schema_failure(self, error):
        failure = super()._schema_failure(error)
        allowed = CLAIM_TYPE_ALLOWED_VALUES if failure.validator == "enum" and failure.path.endswith(".claim_type") else ()
        return SchemaDiagnosticFailureV8(failure.path, failure.validator, allowed)
