"""Staged, non-sensitive diagnostics for Executor output validation."""
from __future__ import annotations

import json
from typing import Any

from jsonschema.exceptions import ValidationError

from campaign_optimizer.contracts.exchange import validate_workflow_exchange
from campaign_optimizer.contracts.validation import (
    ContractValidationError,
    validate_contract_bundle,
    validate_contract_object,
)


class SafeOutputValidationFailure(ContractValidationError):
    """Carries only an allowlisted category/path, never rejected content."""

    def __init__(self, category: str, path: str) -> None:
        self.category = category
        self.path = path
        super().__init__("candidate output rejected")


class DiagnosticOutputGuard:
    def validate(
        self,
        raw_text: str,
        *,
        request: dict[str, Any],
        plan: dict[str, Any],
        review: dict[str, Any],
        context: dict[str, Any],
        retry_count: int,
    ) -> dict[str, Any]:
        try:
            output = json.loads(raw_text)
        except (TypeError, json.JSONDecodeError):
            raise SafeOutputValidationFailure("JSON", "output") from None
        if not isinstance(output, dict):
            raise SafeOutputValidationFailure("JSON", "output.object")
        if output.get("status") != "OK":
            raise SafeOutputValidationFailure("GUARD", "output.status")
        if output.get("retry_count") != retry_count:
            raise SafeOutputValidationFailure("GUARD", "output.retry_count")
        if output.get("fallback_used") is not False:
            raise SafeOutputValidationFailure("GUARD", "output.fallback_used")
        try:
            validate_contract_object("llm_workflow_output", output)
        except ValidationError:
            raise SafeOutputValidationFailure("SCHEMA", "output.schema") from None
        try:
            # Inputs have already passed the static gate, so a bundle failure here
            # is attributable to the candidate's claim/reference bindings.
            validate_contract_bundle(plan, review, context, output)
        except ContractValidationError:
            raise SafeOutputValidationFailure(
                "SOURCE_BINDING", "output.claims"
            ) from None
        try:
            validate_workflow_exchange(request, plan, review, context, output)
        except ContractValidationError:
            raise SafeOutputValidationFailure("EXCHANGE", "output.exchange") from None
        return output
