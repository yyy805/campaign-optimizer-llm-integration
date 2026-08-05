"""Parse and validate model output without exposing unverified text."""

from __future__ import annotations

import json
from typing import Any

from campaign_optimizer.contracts.exchange import validate_workflow_exchange
from campaign_optimizer.contracts.validation import ContractValidationError


class OutputGuard:
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
        except (TypeError, json.JSONDecodeError) as exc:
            raise ContractValidationError("model output is not valid JSON") from exc
        if not isinstance(output, dict):
            raise ContractValidationError("model output must be one JSON object")
        if output.get("status") != "OK":
            raise ContractValidationError("model may only produce OK output")
        if output.get("retry_count") != retry_count:
            raise ContractValidationError("model retry_count does not match backend state")
        if output.get("fallback_used") is not False:
            raise ContractValidationError("model cannot control fallback state")
        review_verdicts = {
            item["review_item_id"]: item["verdict"] for item in review["items"]
        }
        for claim in output.get("claims", []):
            if (
                claim.get("claim_type") == "REVIEW_FIELD"
                and claim.get("field") == "verdict"
                and review_verdicts.get(claim.get("source_id")) != claim.get("value")
            ):
                raise ContractValidationError(
                    "model output verdict conflicts with the committed review"
                )
        validate_workflow_exchange(request, plan, review, context, output)
        return output
