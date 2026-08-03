"""Prompts assembled only from validated request/context structures."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from campaign_optimizer.contracts.validation import validate_contract_object


class PromptBuilder:
    def build(
        self, request: dict[str, Any], context: dict[str, Any]
    ) -> list[dict[str, str]]:
        validate_contract_object("llm_request", request)
        validate_contract_object("llm_context", context)
        payload = {
            "mode": request["mode"],
            "question": request["question"],
            "allowed_intents": request["allowed_intents"],
            "expected_versions": request["expected_versions"],
            "plan": context["plan_context"],
            "review": context["review_context"],
            "allowed_rule_ids": context["allowed_rule_ids"],
            "allowed_plan_item_ids": context["allowed_plan_item_ids"],
            "allowed_fact_ids": context["allowed_fact_ids"],
            "public_rules": context["public_rule_context"],
        }
        messages = [
            {
                "role": "system",
                "content": (
                    "Return one JSON object conforming to llm_workflow_output schema. "
                    "Use only supplied structures and exact IDs/values. Never infer, "
                    "recalculate, or modify actions, numbers, verdicts, rules, facts, "
                    "plan IDs, or limitations. Include every review limitation as a "
                    "claim. Output JSON only."
                ),
            }
        ]
        messages.extend(
            {"role": item["role"], "content": item["content"]}
            for item in request["chat_history"]
        )
        messages.append(
            {
                "role": "user",
                "content": json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            }
        )
        return messages

    def repair(
        self,
        messages: Sequence[Mapping[str, str]],
        invalid_text: str,
        *,
        retry_count: int = 1,
    ) -> list[dict[str, str]]:
        repaired = [dict(message) for message in messages]
        repaired.append({"role": "assistant", "content": invalid_text[:8_000]})
        repaired.append(
            {
                "role": "user",
                "content": (
                    "The previous response failed the contract/authority guard. "
                    f"Return corrected JSON only with retry_count={retry_count}. "
                    "Do not add facts or change any supplied value or ID."
                ),
            }
        )
        return repaired
