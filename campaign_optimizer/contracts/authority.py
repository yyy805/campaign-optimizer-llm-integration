"""本体规则卡的权威发布适配与审核语义校验。"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from campaign_optimizer.ontology.condition_evaluator import (
    EvaluationStatus,
    evaluate_condition,
    missing_required_concepts,
)

from .concept_authority import validate_rule_fact_semantics
from .validation import ContractValidationError

RULES_DIR = Path(__file__).parent.parent / "ontology" / "rules"
PERIOD_ALIASES = {
    "daily": {"daily", "current_day"},
    "7day": {"7day", "current_7_days", "next_7_days"},
    "14day": {"14day", "current_14_days", "next_14_days"},
    "snapshot": {"snapshot", "current_snapshot"},
    "mock": {"mock"},
}
RULE_BEARING_VERDICTS = {"SUPPORT", "CONFLICT", "NOT_APPLICABLE"}
CONFIDENCE_BLOCKERS = {
    "runtime_confidence_state",
    "active_confidence_state",
    "minimum_usable_confidence",
    "finite_runtime_confidence",
    "base_confidence",
}


def load_rule_card(rule_id: str, rules_dir: Path = RULES_DIR) -> dict[str, Any]:
    """按规则ID读取唯一权威规则卡，不接受请求方自报规则。"""
    path = rules_dir / f"{rule_id}.json"
    if not path.is_file():
        raise ContractValidationError(f"权威规则库中不存在{rule_id}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractValidationError(f"无法读取权威规则卡{rule_id}: {exc}") from exc


def latest_rule_version(card: dict[str, Any]) -> str:
    """提取规则卡最新版本；缺少版本时禁止发布。"""
    history = card.get("version_history")
    if not isinstance(history, list) or not history:
        raise ContractValidationError(
            f"权威规则{card.get('rule_id', '<unknown>')}缺少version_history"
        )
    version = history[-1].get("version")
    if not isinstance(version, str) or not version:
        raise ContractValidationError(
            f"权威规则{card.get('rule_id', '<unknown>')}最新版本非法"
        )
    return version


def _evaluate_trigger(
    condition: dict[str, Any], facts_by_concept: dict[str, dict[str, Any]]
) -> bool | None:
    """Backward-compatible bool/None projection of the shared evaluator."""
    status = evaluate_condition(condition, facts_by_concept).status
    if status == EvaluationStatus.MATCHED:
        return True
    if status == EvaluationStatus.NOT_MATCHED:
        return False
    return None


def public_rule_from_card(card: dict[str, Any]) -> dict[str, Any]:
    """确定性导出允许交给LLM的公开规则字段。"""
    grain = card["evaluation_grain"]
    policy = card["review_policy"]
    return {
        "rule_id": card["rule_id"],
        "rule_version": latest_rule_version(card),
        "status": card["status"],
        "name": card["name"],
        "definition": card["diagnosis"],
        "applicable_scope": [f"time={grain['time']}", f"entity={grain['entity']}"],
        "review_policy": {
            "mode": policy["mode"],
            "supported_plan_actions": list(policy["supported_plan_actions"]),
            "conflicting_plan_actions": list(policy["conflicting_plan_actions"]),
            "otherwise": policy["otherwise"],
        },
        "limitations": list(card.get("known_limitations", [])),
    }


def _validate_insufficient_item(
    item: dict[str, Any],
    card: dict[str, Any],
    plan_items: dict[str, dict[str, Any]],
    review_facts: dict[str, dict[str, Any]],
    errors: list[str],
) -> None:
    item_id = item["review_item_id"]
    plan_item = plan_items.get(item["plan_item_id"])
    if plan_item is None:
        errors.append(f"{item_id} references an unknown plan item")
        return
    if plan_item["entity_type"] != card["evaluation_grain"]["entity"]:
        errors.append(f"{item_id} insufficient review has the wrong entity grain")

    unknown_fact_ids = sorted(set(item["matched_fact_ids"]) - set(review_facts))
    if unknown_fact_ids:
        errors.append(f"{item_id} references unknown review facts: {', '.join(unknown_fact_ids)}")
    matched_facts = [
        review_facts[fact_id]
        for fact_id in item["matched_fact_ids"]
        if fact_id in review_facts
    ]
    if any(fact["plan_item_id"] != item["plan_item_id"] for fact in matched_facts):
        errors.append(f"{item_id} references facts from another plan item")
    names = [fact["name"] for fact in matched_facts]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        errors.append(f"{item_id} repeats rule concepts: {', '.join(duplicates)}")

    facts_by_concept = {fact["name"]: fact for fact in matched_facts}
    evaluation = evaluate_condition(card["trigger_condition"], facts_by_concept)
    if evaluation.status == EvaluationStatus.NOT_MATCHED:
        errors.append(f"{item_id} cannot claim insufficient evidence for a non-matched rule")

    expected_missing = missing_required_concepts(
        card["trigger_condition"], set(facts_by_concept)
    )
    if set(item["missing_evidence"]) != expected_missing:
        errors.append(f"{item_id} does not truthfully report missing evidence")
    allowed_parameters = set(evaluation.missing_rule_parameters).union(CONFIDENCE_BLOCKERS)
    reported_parameters = set(item["missing_rule_parameters"])
    if not reported_parameters.issubset(allowed_parameters):
        errors.append(f"{item_id} reports unsupported missing rule parameters")
    if evaluation.status == EvaluationStatus.MATCHED and not reported_parameters:
        errors.append(f"{item_id} matched the rule and lacks a confidence blocker")

    errors.extend(
        f"{item_id}: {error}"
        for error in validate_rule_fact_semantics(card["trigger_condition"], matched_facts)
    )
    grain = card["evaluation_grain"]["time"]
    allowed_periods = PERIOD_ALIASES.get(grain, {grain})
    if any(fact["period"] not in allowed_periods for fact in matched_facts):
        errors.append(f"{item_id} insufficient evidence uses an incompatible period")

    base = item["base_confidence"]
    card_base = card.get("confidence")
    if base is not None and (
        not isinstance(card_base, (int, float))
        or not math.isclose(base, card_base, rel_tol=0, abs_tol=1e-9)
    ):
        errors.append(f"{item_id} base confidence does not match the rule card")


def validate_authoritative_review(
    plan: dict[str, Any],
    review: dict[str, Any],
    context: dict[str, Any],
    *,
    rules_dir: Path = RULES_DIR,
) -> None:
    """验证评价引用真实规则，并验证规则确实命中相应事实与实体。"""
    errors: list[str] = []
    plan_items = {item["plan_item_id"]: item for item in plan["items"]}
    review_facts = {fact["fact_id"]: fact for fact in plan["review_evidence"]}
    public_rules = {
        (rule["rule_id"], rule["rule_version"]): rule
        for rule in context["public_rule_context"]
    }

    for item in review["items"]:
        rule_id = item["rule_id"]
        if rule_id is None:
            continue
        try:
            card = load_rule_card(rule_id, rules_dir)
            version = latest_rule_version(card)
            expected_public = public_rule_from_card(card)
        except ContractValidationError as exc:
            errors.append(str(exc))
            continue

        if item["rule_version"] != version:
            errors.append(
                f"{item['review_item_id']}引用{rule_id}版本{item['rule_version']}，"
                f"权威最新版本为{version}"
            )
        actual_public = public_rules.get((rule_id, item["rule_version"]))
        if actual_public != expected_public:
            errors.append(f"{rule_id}公开规则上下文不是由权威规则卡确定性导出")

        if item["verdict"] == "INSUFFICIENT_EVIDENCE":
            _validate_insufficient_item(item, card, plan_items, review_facts, errors)
            continue
        if item["verdict"] not in RULE_BEARING_VERDICTS:
            continue

        plan_item = plan_items.get(item["plan_item_id"])
        if plan_item is None:
            continue
        expected_entity = card["evaluation_grain"]["entity"]
        if plan_item["entity_type"] != expected_entity:
            errors.append(
                f"{item['review_item_id']}的方案实体{plan_item['entity_type']}"
                f"与{rule_id}规则粒度{expected_entity}不一致"
            )

        policy = card["review_policy"]
        supported = set(policy["supported_plan_actions"])
        conflicting = set(policy["conflicting_plan_actions"])
        if supported.intersection(conflicting):
            errors.append(f"{rule_id}的review_policy支持与冲突动作不能重叠")
        action = plan_item["action"]
        expected_verdict = (
            "SUPPORT" if action in supported
            else "CONFLICT" if action in conflicting
            else policy["otherwise"]
        )
        if item["verdict"] != expected_verdict:
            errors.append(
                f"{item['review_item_id']}对动作{action}应为{expected_verdict}，"
                f"不能标为{item['verdict']}"
            )

        card_confidence = card.get("confidence")
        minimum_usable = card["confidence_model"]["thresholds"]["minimum_usable"]
        base = item["base_confidence"]
        runtime = item["runtime_confidence"]
        if (
            not isinstance(card_confidence, (int, float))
            or not isinstance(base, (int, float))
            or not math.isclose(base, card_confidence, rel_tol=0, abs_tol=1e-9)
        ):
            errors.append(f"{item['review_item_id']}的base_confidence必须等于规则卡快照")
        if not isinstance(runtime, (int, float)) or runtime < minimum_usable:
            errors.append(f"{item['review_item_id']}的runtime_confidence低于规则最低可用阈值")

        matched_facts = [
            review_facts[fact_id]
            for fact_id in item["matched_fact_ids"]
            if fact_id in review_facts
        ]
        concepts = [fact["name"] for fact in matched_facts]
        duplicate_concepts = sorted({name for name in concepts if concepts.count(name) > 1})
        if duplicate_concepts:
            errors.append(
                f"{item['review_item_id']}同一规则概念不能重复提供事实: "
                + ", ".join(duplicate_concepts)
            )
        facts_by_concept = {fact["name"]: fact for fact in matched_facts}
        missing_concepts = missing_required_concepts(
            card.get("trigger_condition", {}), set(facts_by_concept)
        )
        if missing_concepts:
            errors.append(
                f"{item['review_item_id']}缺少{rule_id}所需概念: "
                + ", ".join(sorted(missing_concepts))
            )
        trigger_result = _evaluate_trigger(card["trigger_condition"], facts_by_concept)
        if trigger_result is False:
            errors.append(f"{item['review_item_id']}提供的事实未命中{rule_id}触发条件")
        elif trigger_result is None:
            errors.append(
                f"{item['review_item_id']}无法完整计算{rule_id}触发条件，"
                "可能缺少事实或baseline"
            )

        errors.extend(
            f"{item['review_item_id']}: {error}"
            for error in validate_rule_fact_semantics(
                card.get("trigger_condition", {}), matched_facts
            )
        )

        grain = card["evaluation_grain"]["time"]
        allowed_periods = PERIOD_ALIASES.get(grain, {grain})
        wrong_periods = sorted(
            {fact["period"] for fact in matched_facts if fact["period"] not in allowed_periods}
        )
        if wrong_periods:
            errors.append(
                f"{item['review_item_id']}证据周期与{rule_id}的{grain}粒度不一致: "
                + ", ".join(wrong_periods)
            )
        if base is not None and runtime is not None and (base <= 0 or runtime <= 0):
            errors.append(f"{item['review_item_id']}的规则评价置信度必须大于0")

    if errors:
        raise ContractValidationError("; ".join(errors))
