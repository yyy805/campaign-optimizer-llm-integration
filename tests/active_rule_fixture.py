from __future__ import annotations

import copy
import json
from pathlib import Path

from campaign_optimizer.contracts.authority import load_rule_card, public_rule_from_card

ROOT = Path(__file__).parent / "fixtures" / "plan_a"


def active_rule_bundle() -> dict[str, dict]:
    load = lambda name: json.loads((ROOT / name).read_text(encoding="utf-8"))
    plan = load("final_plan.demo.json")
    review = load("ontology_review.demo.json")
    context = load("llm_context.demo.json")
    output = load("llm_workflow_output.demo.json")
    facts = [
        {"fact_id":"review_fact_001","plan_item_id":"plan_item_001","entity_type":"campaign","entity_id":"Sponsored Products","name":"acos","value":0.40,"baseline_value":0.20,"baseline_source":"account_history","baseline_period":"prior_14_days","unit":"ratio","period":"next_14_days","source":"demo_mta_output","scope":"ontology_review"},
        {"fact_id":"review_fact_002","plan_item_id":"plan_item_001","entity_type":"campaign","entity_id":"Sponsored Products","name":"ctr","value":0.01,"baseline_value":0.03,"baseline_source":"account_history","baseline_period":"prior_14_days","unit":"ratio","period":"next_14_days","source":"demo_platform_output","scope":"ontology_review"},
    ]
    plan["review_evidence"] = facts
    item = review["items"][0]
    item.update(review_item_id="review_item_001", verdict="CONFLICT", rule_id="R1", rule_version="1.2-contract-hardening", base_confidence=0.65, runtime_confidence=0.65, matched_fact_ids=["review_fact_001", "review_fact_002"], limitations=["Account baselines require production recalibration."])
    review["overall_verdict"] = "CONFLICT"
    context["plan_context"] = copy.deepcopy(plan)
    context["review_context"] = copy.deepcopy(review)
    context["allowed_fact_ids"] = ["decision_fact_001", "review_fact_001", "review_fact_002"]
    context["allowed_rule_ids"] = ["R1"]
    context["public_rule_context"] = [public_rule_from_card(load_rule_card("R1"))]
    output.update(
        answer=(
            "这是Demo模拟结果。系统建议在2026年8月1日至8月14日期间，"
            "将Sponsored Products预算从1000美元提高到1100美元，即增加10%。"
            "本体评价为冲突：R1 1.2-contract-hardening检查到ACOS和CTR相对历史基线异常。"
            "公开的预测ROAS为4.2。Account baselines require production recalibration."
        ),
        claims=[
            {"claim_id":"claim_001","claim_type":"PLAN_FIELD","source_id":"plan_item_001","field":"entity_id","value":"Sponsored Products"},
            {"claim_id":"claim_002","claim_type":"PLAN_FIELD","source_id":"plan_item_001","field":"delta_pct","value":10},
            {"claim_id":"claim_003","claim_type":"PLAN_FIELD","source_id":"plan_item_001","field":"current_budget","value":1000},
            {"claim_id":"claim_004","claim_type":"PLAN_FIELD","source_id":"plan_item_001","field":"recommended_budget","value":1100},
            {"claim_id":"claim_005","claim_type":"REVIEW_FIELD","source_id":"review_item_001","field":"verdict","value":"CONFLICT"},
            {"claim_id":"claim_006","claim_type":"FACT_VALUE","source_id":"decision_fact_001","field":"value","value":4.2},
            {"claim_id":"claim_007","claim_type":"RULE_FIELD","source_id":"R1","field":"status","value":"ACTIVE"},
            {"claim_id":"claim_008","claim_type":"PLAN_PERIOD_FIELD","source_id":"plan_item_001","field":"start_date","value":"2026-08-01"},
            {"claim_id":"claim_009","claim_type":"PLAN_PERIOD_FIELD","source_id":"plan_item_001","field":"end_date","value":"2026-08-14"},
            {"claim_id":"claim_010","claim_type":"RULE_FIELD","source_id":"R1","field":"rule_version","value":"1.2-contract-hardening"},
            {"claim_id":"claim_011","claim_type":"FACT_VALUE","source_id":"review_fact_001","field":"value","value":0.40},
            {"claim_id":"claim_012","claim_type":"REVIEW_FIELD","source_id":"review_item_001","field":"limitations","value":"Account baselines require production recalibration."},
            {"claim_id":"claim_013","claim_type":"FACT_VALUE","source_id":"review_fact_002","field":"value","value":0.01},
            {"claim_id":"claim_014","claim_type":"REVIEW_FIELD","source_id":"review_item_001","field":"base_confidence","value":0.65},
            {"claim_id":"claim_015","claim_type":"REVIEW_FIELD","source_id":"review_item_001","field":"runtime_confidence","value":0.65},
        ],
        facts_used=["decision_fact_001", "review_fact_001", "review_fact_002"],
        rule_ids_used=["R1"],
        plan_item_ids_used=["plan_item_001"],
        limitations_included=True,
    )
    return {"plan": plan, "review": review, "context": context, "output": output}
