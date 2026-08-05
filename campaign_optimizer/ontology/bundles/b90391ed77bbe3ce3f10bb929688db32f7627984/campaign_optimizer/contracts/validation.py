"""方案A运行时契约校验。

本模块是测试与后端共同使用的唯一语义Gate。Schema负责对象形状，
本模块负责跨对象ID、白名单、评价聚合、动作数值和结构化声明保真。
"""
from __future__ import annotations

import json
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft7Validator, FormatChecker
from referencing import Registry, Resource

SCHEMAS_DIR = Path(__file__).parent.parent / "schemas"
SCHEMA_FILES = {
    "final_plan": "final_plan.schema.json",
    "ontology_review": "ontology_review.schema.json",
    "llm_context": "llm_context.schema.json",
    "llm_request": "llm_request.schema.json",
    "llm_workflow_output": "llm_workflow_output.schema.json",
    "feedback_event": "feedback_event.schema.json",
    "plan_decision_event": "plan_decision_event.schema.json",
    "confidence_state": "confidence_state.schema.json",
    "feedback_policy": "feedback_policy.schema.json",
}
VERDICT_PRIORITY = {
    "SUPPORT": 0,
    "NOT_APPLICABLE": 1,
    "UNVERIFIED": 2,
    "INSUFFICIENT_EVIDENCE": 3,
    "CONFLICT": 4,
}
FIXED_NON_OK_ANSWERS = {
    "FORBIDDEN_MODEL_INTERNAL": "当前功能不能解释模型内部计算、公式、代码或训练过程。",
    "UNSUPPORTED_WHAT_IF": "当前版本暂不支持修改预算后重新生成方案。",
    "OUT_OF_SCOPE": "当前功能只能解释本次方案和本体评价。",
    "SYSTEM_FALLBACK": "解释服务暂时不可用，请稍后重试。",
}


class ContractValidationError(ValueError):
    """跨对象契约或保真约束失败。"""


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _schemas() -> dict[str, dict[str, Any]]:
    return {
        name: _load_json(SCHEMAS_DIR / filename)
        for name, filename in SCHEMA_FILES.items()
    }


@lru_cache(maxsize=1)
def _registry() -> Registry:
    return Registry().with_resources(
        (schema["$id"], Resource.from_contents(schema))
        for schema in _schemas().values()
    )


def validate_contract_object(name: str, payload: dict[str, Any]) -> None:
    """按正式Draft-07 Schema校验单个契约对象。"""
    if name not in SCHEMA_FILES:
        raise KeyError(f"未知契约名称: {name}")
    Draft7Validator(
        _schemas()[name],
        registry=_registry(),
        format_checker=FormatChecker(),
    ).validate(payload)


def aggregate_verdict(items: list[dict[str, Any]]) -> str:
    """按保守优先级确定整体评价，禁止交给LLM裁决。"""
    if not items:
        raise ContractValidationError("ontology_review.items不能为空")
    return max(
        (item["verdict"] for item in items),
        key=VERDICT_PRIORITY.__getitem__,
    )


def _validate_action(item: dict[str, Any], errors: list[str]) -> None:
    action = item["action"]
    delta = item["delta_pct"]
    current = item["current_budget"]
    recommended = item["recommended_budget"]

    if action == "increase_budget" and not (delta > 0 and recommended > current):
        errors.append(f"{item['plan_item_id']}增加预算的方向或delta_pct不一致")
    elif action == "decrease_budget" and not (delta < 0 and recommended < current):
        errors.append(f"{item['plan_item_id']}减少预算的方向或delta_pct不一致")
    elif action == "keep_budget" and not (delta == 0 and recommended == current):
        errors.append(f"{item['plan_item_id']}保持预算时数值必须不变")

    expected = current * (1 + delta / 100)
    if abs(expected - recommended) > 0.01:
        errors.append(f"{item['plan_item_id']}推荐预算与delta_pct不一致")


def _validate_review_item(
    item: dict[str, Any],
    review_facts: dict[str, dict[str, Any]],
    errors: list[str],
) -> None:
    verdict = item["verdict"]
    matched = set(item["matched_fact_ids"])
    missing_evidence = item["missing_evidence"]
    missing_parameters = item["missing_rule_parameters"]

    if not matched.issubset(review_facts):
        errors.append(f"{item['review_item_id']}只能引用review_evidence")
    for fact_id in matched.intersection(review_facts):
        fact = review_facts[fact_id]
        if fact["plan_item_id"] != item["plan_item_id"]:
            errors.append(f"{item['review_item_id']}不能跨方案条目引用审核事实")
        if fact["value"] is None:
            errors.append(f"{item['review_item_id']}不能把空值作为命中证据")

    if verdict in {"SUPPORT", "CONFLICT", "NOT_APPLICABLE"}:
        if not matched:
            errors.append(f"{item['review_item_id']}的{verdict}必须引用审核事实")
        if missing_evidence or missing_parameters:
            errors.append(f"{item['review_item_id']}的{verdict}不能携带缺失项")
        if item["base_confidence"] is None or item["runtime_confidence"] is None:
            errors.append(f"{item['review_item_id']}的{verdict}必须包含置信度快照")
    elif verdict == "INSUFFICIENT_EVIDENCE":
        if not missing_evidence and not missing_parameters:
            errors.append(f"{item['review_item_id']}必须列出证据不足的阻塞原因")
    elif verdict == "UNVERIFIED":
        if any(
            [
                item["rule_id"] is not None,
                item["rule_version"] is not None,
                item["base_confidence"] is not None,
                item["runtime_confidence"] is not None,
                bool(matched),
                bool(missing_evidence),
                bool(missing_parameters),
            ]
        ):
            errors.append(f"{item['review_item_id']}的UNVERIFIED字段组合非法")


def validate_contract_bundle(
    plan: dict[str, Any],
    review: dict[str, Any],
    context: dict[str, Any],
    output: dict[str, Any] | None = None,
) -> None:
    """执行四份契约的Schema校验和跨对象确定性Gate。"""
    validate_contract_object("final_plan", plan)
    validate_contract_object("ontology_review", review)
    validate_contract_object("llm_context", context)
    if output is not None:
        validate_contract_object("llm_workflow_output", output)

    errors: list[str] = []

    start = date.fromisoformat(plan["period"]["start_date"])
    end = date.fromisoformat(plan["period"]["end_date"])
    if (end - start).days != 13:
        errors.append("next_14_days必须首尾合计14个自然日")

    plan_items = {item["plan_item_id"]: item for item in plan["items"]}
    if len(plan_items) != len(plan["items"]):
        errors.append("plan_item_id必须唯一")
    for item in plan["items"]:
        _validate_action(item, errors)

    decision_facts = {fact["fact_id"]: fact for fact in plan["decision_evidence"]}
    review_facts = {fact["fact_id"]: fact for fact in plan["review_evidence"]}
    all_facts_list = plan["decision_evidence"] + plan["review_evidence"]
    facts = {fact["fact_id"]: fact for fact in all_facts_list}
    if len(facts) != len(all_facts_list):
        errors.append("fact_id必须在两类证据中全局唯一")
    if len(decision_facts) != len(plan["decision_evidence"]):
        errors.append("decision_evidence中的fact_id必须唯一")
    if len(review_facts) != len(plan["review_evidence"]):
        errors.append("review_evidence中的fact_id必须唯一")

    for fact in all_facts_list:
        item = plan_items.get(fact["plan_item_id"])
        if item is None:
            errors.append(f"{fact['fact_id']}引用不存在的plan_item_id")
            continue
        if (fact["entity_type"], fact["entity_id"]) != (
            item["entity_type"],
            item["entity_id"],
        ):
            errors.append(f"{fact['fact_id']}的实体与方案条目不一致")

    if review["plan_id"] != plan["plan_id"]:
        errors.append("ontology_review.plan_id与final_plan.plan_id不一致")

    review_items = {item["review_item_id"]: item for item in review["items"]}
    if len(review_items) != len(review["items"]):
        errors.append("review_item_id必须唯一")

    rule_versions: dict[str, str] = {}
    review_targets: set[tuple[str, str | None]] = set()
    reviewed_plan_item_ids: set[str] = set()
    for item in review["items"]:
        if item["plan_item_id"] not in plan_items:
            errors.append(f"{item['review_item_id']}引用不存在的plan_item_id")
        reviewed_plan_item_ids.add(item["plan_item_id"])
        review_target = (item["plan_item_id"], item["rule_id"])
        if review_target in review_targets:
            errors.append("同一plan_item_id与rule_id组合只能有一条评价")
        review_targets.add(review_target)
        _validate_review_item(item, review_facts, errors)
        if item["rule_id"] is not None:
            previous = rule_versions.setdefault(item["rule_id"], item["rule_version"])
            if previous != item["rule_version"]:
                errors.append("同一审核快照内一个rule_id只能对应一个rule_version")

    missing_review_ids = set(plan_items) - reviewed_plan_item_ids
    if missing_review_ids:
        errors.append(
            "ontology_review必须覆盖每个plan_item: "
            + ", ".join(sorted(missing_review_ids))
        )

    if review["overall_verdict"] != aggregate_verdict(review["items"]):
        errors.append("overall_verdict不符合保守优先级")

    if context["plan_context"] != plan:
        errors.append("llm_context.plan_context不是final_plan只读快照")
    if context["review_context"] != review:
        errors.append("llm_context.review_context不是ontology_review只读快照")

    expected_plan_ids = set(plan_items)
    expected_fact_ids = set(facts)
    expected_rule_versions = set(rule_versions.items())
    expected_rule_ids = set(rule_versions)

    if set(context["allowed_plan_item_ids"]) != expected_plan_ids:
        errors.append("allowed_plan_item_ids必须严格等于方案条目ID集合")
    if set(context["allowed_fact_ids"]) != expected_fact_ids:
        errors.append("allowed_fact_ids必须严格等于公开事实ID集合")
    if set(context["allowed_rule_ids"]) != expected_rule_ids:
        errors.append("allowed_rule_ids必须严格等于审核规则ID集合")

    public_rules = {
        (rule["rule_id"], rule["rule_version"]): rule
        for rule in context["public_rule_context"]
    }
    if len(public_rules) != len(context["public_rule_context"]):
        errors.append("public_rule_context不能包含重复的规则版本")
    if set(public_rules) != expected_rule_versions:
        errors.append("public_rule_context必须且只能覆盖审核引用的规则版本")
    public_rules_by_id = {
        rule_id: rule for (rule_id, _), rule in public_rules.items()
    }

    for item in review["items"]:
        if item["verdict"] in {"SUPPORT", "CONFLICT", "NOT_APPLICABLE"}:
            public_rule = public_rules_by_id.get(item["rule_id"])
            if public_rule is None or public_rule["status"] != "ACTIVE":
                errors.append(f"{item['review_item_id']}的{item['verdict']}只能引用ACTIVE规则")

    if output is not None:
        if output["status"] in {"REFUSED", "FALLBACK"}:
            expected_answer = FIXED_NON_OK_ANSWERS[output["intent"]]
            if output["answer"] != expected_answer:
                errors.append("REFUSED/FALLBACK必须使用后端固定文案")

        if not set(output["facts_used"]).issubset(context["allowed_fact_ids"]):
            errors.append("Workflow引用了白名单外的fact_id")
        if not set(output["rule_ids_used"]).issubset(context["allowed_rule_ids"]):
            errors.append("Workflow引用了白名单外的rule_id")
        if not set(output["plan_item_ids_used"]).issubset(
            context["allowed_plan_item_ids"]
        ):
            errors.append("Workflow引用了白名单外的plan_item_id")

        claim_ids = [claim["claim_id"] for claim in output["claims"]]
        if len(set(claim_ids)) != len(claim_ids):
            errors.append("claim_id必须唯一")

        claimed_fact_ids: set[str] = set()
        claimed_rule_ids: set[str] = set()
        claimed_plan_item_ids: set[str] = set()
        for claim in output["claims"]:
            claim_type = claim["claim_type"]
            source_id = claim["source_id"]
            if claim_type == "PLAN_FIELD":
                source = plan_items.get(source_id)
                claimed_plan_item_ids.add(source_id)
                if source_id not in output["plan_item_ids_used"]:
                    errors.append(f"{claim['claim_id']}未同步plan_item_ids_used")
            elif claim_type == "PLAN_PERIOD_FIELD":
                source = plan["period"] if source_id in plan_items else None
                claimed_plan_item_ids.add(source_id)
                if source_id not in output["plan_item_ids_used"]:
                    errors.append(f"{claim['claim_id']}未同步plan_item_ids_used")
            elif claim_type == "REVIEW_FIELD":
                source = review_items.get(source_id)
                if source is not None:
                    claimed_plan_item_ids.add(source["plan_item_id"])
                    if source["plan_item_id"] not in output["plan_item_ids_used"]:
                        errors.append(f"{claim['claim_id']}未同步plan_item_ids_used")
                    if (
                        source["rule_id"] is not None
                        and source["rule_id"] not in output["rule_ids_used"]
                    ):
                        errors.append(f"{claim['claim_id']}未同步rule_ids_used")
                    if source["rule_id"] is not None:
                        claimed_rule_ids.add(source["rule_id"])
            elif claim_type == "FACT_VALUE":
                source = facts.get(source_id)
                claimed_fact_ids.add(source_id)
                if source is not None:
                    claimed_plan_item_ids.add(source["plan_item_id"])
                if source_id not in output["facts_used"]:
                    errors.append(f"{claim['claim_id']}未同步facts_used")
            elif claim_type == "RULE_FIELD":
                source = public_rules_by_id.get(source_id)
                claimed_rule_ids.add(source_id)
                if source_id not in output["rule_ids_used"]:
                    errors.append(f"{claim['claim_id']}未同步rule_ids_used")
            else:
                source = None

            if source is None:
                errors.append(f"{claim['claim_id']}引用不存在的source_id")
                continue
            if claim["field"] not in source:
                errors.append(f"{claim['claim_id']}引用不存在的字段")
                continue
            source_value = source[claim["field"]]
            if isinstance(source_value, list):
                matches = claim["value"] in source_value
            else:
                matches = claim["value"] == source_value
            if not matches:
                errors.append(f"{claim['claim_id']}篡改了结构化事实")

        if set(output["facts_used"]) != claimed_fact_ids:
            errors.append("facts_used必须严格等于claims实际引用的事实集合")
        if set(output["rule_ids_used"]) != claimed_rule_ids:
            errors.append("rule_ids_used必须严格等于claims实际引用的规则集合")
        if set(output["plan_item_ids_used"]) != claimed_plan_item_ids:
            errors.append("plan_item_ids_used必须严格等于claims实际引用的方案条目集合")

        has_limitations = any(item["limitations"] for item in review["items"])
        expected_limitations = {
            (item["review_item_id"], limitation)
            for item in review["items"]
            for limitation in item["limitations"]
        }
        claimed_limitations = {
            (claim["source_id"], claim["value"])
            for claim in output["claims"]
            if claim["claim_type"] == "REVIEW_FIELD"
            and claim["field"] == "limitations"
        }
        if output["status"] == "OK" and has_limitations:
            if (
                not output["limitations_included"]
                or not expected_limitations.issubset(claimed_limitations)
            ):
                errors.append("存在重要限制时OK输出必须完整披露全部限制claim")

    if errors:
        raise ContractValidationError("; ".join(errors))
