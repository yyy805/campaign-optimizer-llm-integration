"""方案A生产调用层：绑定请求、权威规则、版本和LLM输出。"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

from .authority import RULES_DIR, validate_authoritative_review
from .validation import (
    ContractValidationError,
    validate_contract_bundle,
    validate_contract_object,
)

MAX_CONTEXT_BYTES = 512 * 1024
MAX_OUTPUT_BYTES = 256 * 1024
HIGH_RISK_PLAN_FIELDS = {"delta_pct", "current_budget", "recommended_budget"}
PERCENT_PATTERN = re.compile(r"(?<![\d.])(-?\d+(?:\.\d+)?)\s*%")
ACTION_PERCENT_PATTERN = re.compile(
    r"(增加|提高|上调|减少|降低|下调)\s*(-?\d+(?:\.\d+)?)\s*%"
)


def _serialized_size(payload: dict[str, Any]) -> int:
    return len(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )


def _number_pattern(value: float | int) -> re.Pattern[str]:
    numeric = float(value)
    if numeric.is_integer():
        token = str(int(numeric))
        return re.compile(rf"(?<![\d.]){re.escape(token)}(?:\.0+)?(?![\d.])")
    token = format(numeric, ".15g")
    return re.compile(rf"(?<![\d.]){re.escape(token)}(?![\d.])")


def validate_answer_numeric_grounding(output: dict[str, Any]) -> None:
    """校验最容易造成业务损失的预算与百分比数字，不宣称理解全部语义。"""
    if output["status"] != "OK":
        return
    answer = output["answer"]
    relevant_claims = [
        claim
        for claim in output["claims"]
        if claim["claim_type"] == "PLAN_FIELD"
        and claim["field"] in HIGH_RISK_PLAN_FIELDS
        and isinstance(claim["value"], (int, float))
        and not isinstance(claim["value"], bool)
    ]
    errors: list[str] = []
    for claim in relevant_claims:
        if not _number_pattern(claim["value"]).search(answer):
            errors.append(
                f"answer未披露{claim['source_id']}.{claim['field']}={claim['value']}"
            )

    grounded_percentages = {
        float(claim["value"])
        for claim in relevant_claims
        if claim["field"] == "delta_pct"
    }
    for matched in ACTION_PERCENT_PATTERN.finditer(answer):
        direction, raw_value = matched.groups()
        value = float(raw_value)
        signed_value = (
            abs(value)
            if direction in {"增加", "提高", "上调"}
            else -abs(value)
        )
        if not any(
            math.isclose(signed_value, expected, abs_tol=1e-9)
            for expected in grounded_percentages
        ):
            errors.append(
                f"answer中的动作幅度{direction}{raw_value}%没有delta_pct claim支持"
            )
    for claim in output["claims"]:
        value = claim["value"]
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            numeric = float(value)
            grounded_percentages.add(numeric)
            if 0 <= abs(numeric) <= 1:
                grounded_percentages.add(numeric * 100)
        elif isinstance(value, str):
            grounded_percentages.update(
                float(match.group(1)) for match in PERCENT_PATTERN.finditer(value)
            )
    for matched in PERCENT_PATTERN.finditer(answer):
        value = float(matched.group(1))
        if not any(
            math.isclose(value, expected, abs_tol=1e-9)
            for expected in grounded_percentages
        ):
            errors.append(f"answer出现无claim支持的百分比{value}%")

    if errors:
        raise ContractValidationError("; ".join(errors))


def validate_workflow_exchange(
    request: dict[str, Any],
    plan: dict[str, Any],
    review: dict[str, Any],
    context: dict[str, Any],
    output: dict[str, Any],
    *,
    rules_dir: Path = RULES_DIR,
) -> None:
    """生产后端必须调用的完整Gate；底层bundle校验不能替代本函数。"""
    validate_contract_object("llm_request", request)
    validate_contract_bundle(plan, review, context, output)

    errors: list[str] = []
    if request["context_id"] != context["context_id"]:
        errors.append("llm_request.context_id与llm_context.context_id不一致")
    if (
        output["intent"] != "SYSTEM_FALLBACK"
        and output["intent"] not in request["allowed_intents"]
    ):
        errors.append("Workflow输出intent不属于本次请求白名单")

    expected = request["expected_versions"]
    actual = {
        "workflow_version": output["workflow_version"],
        "prompt_version": output["prompt_version"],
        "knowledge_base_version": output["knowledge_base_version"],
    }
    if actual != expected:
        errors.append("Workflow/Prompt/知识库版本与后端期望版本不一致")

    if _serialized_size(context) > MAX_CONTEXT_BYTES:
        errors.append(f"llm_context超过{MAX_CONTEXT_BYTES}字节上限")
    if _serialized_size(output) > MAX_OUTPUT_BYTES:
        errors.append(f"llm_workflow_output超过{MAX_OUTPUT_BYTES}字节上限")

    if errors:
        raise ContractValidationError("; ".join(errors))
    validate_authoritative_review(plan, review, context, rules_dir=rules_dir)
    validate_answer_numeric_grounding(output)
