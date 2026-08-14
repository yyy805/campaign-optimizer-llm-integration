from __future__ import annotations

import json

import pytest

from app.domain.models import Disposition, Outcome, ReviewCreate
from app.errors import AppError
from app.ontology import load_ontology
from app.services.review_engine import ReviewEngine
from tests.conftest import ONTOLOGY_ROOT


@pytest.fixture(scope="module")
def engine() -> ReviewEngine:
    return ReviewEngine(load_ontology(ONTOLOGY_ROOT))


def request(rule_ids: list[str], inputs: list[dict], **extra) -> ReviewCreate:
    grain = "touchpoint" if any(rule_id in {"R3", "R5"} for rule_id in rule_ids) else "campaign"
    value = {
        "client_id": "demo_client_001",
        "entity": {"grain": grain, "id": "engine-test"},
        "candidate_rules": rule_ids,
        "inputs": inputs,
    }
    value.update(extra)
    return ReviewCreate.model_validate(value)


@pytest.mark.parametrize(
    ("rule_id", "inputs", "expected_disposition"),
    [
        ("R1", [{"concept": "acos", "value": .50, "baseline": .35}, {"concept": "ctr", "value": .01, "baseline": .02}], Disposition.REVIEW),
        ("R2", [{"concept": "impressions_growth", "value": .21}], Disposition.MANUAL_CONFIRM),
        ("R3", [{"concept": "mta_roas", "value": 1.6, "baseline": 1.0}], Disposition.REVIEW),
        ("R4", [{"concept": "roas", "value": .9}], Disposition.REVIEW),
        ("R5", [{"concept": "contribution_share", "value": .08}, {"concept": "spend_share", "value": .25}, {"concept": "attribution_divergence", "value": .05}], Disposition.REVIEW),
        ("R6", [{"concept": "cvr", "value": .04, "baseline": .1}], Disposition.MANUAL_CONFIRM),
    ],
)
def test_active_rules_match(engine, rule_id, inputs, expected_disposition):
    result = engine.evaluate(request([rule_id], inputs))
    assert result.outcome == Outcome.MATCH
    assert result.matched_rules == [rule_id]
    assert result.disposition == expected_disposition


@pytest.mark.parametrize(
    ("rule_id", "inputs"),
    [
        ("R1", [{"concept": "acos", "value": .455, "baseline": .35}, {"concept": "ctr", "value": .012, "baseline": .02}]),
        ("R2", [{"concept": "impressions_growth", "value": .20}]),
        ("R3", [{"concept": "mta_roas", "value": 1.5, "baseline": 1.0}]),
        ("R4", [{"concept": "roas", "value": 1.0}]),
        ("R6", [{"concept": "cvr", "value": .05, "baseline": .1}]),
    ],
)
def test_strict_boundaries_do_not_match(engine, rule_id, inputs):
    result = engine.evaluate(request([rule_id], inputs))
    assert result.outcome == Outcome.NO_COVERAGE
    assert result.disposition == Disposition.NO_ACTION


def test_r5_inclusive_divergence_boundary_matches(engine):
    result = engine.evaluate(request(["R5"], [
        {"concept": "contribution_share", "value": .08},
        {"concept": "spend_share", "value": .25},
        {"concept": "attribution_divergence", "value": .05},
    ]))
    assert result.outcome == Outcome.MATCH


def test_only_approved_r1_r2_conflict_has_precedence(engine):
    result = engine.evaluate(request(["R1", "R2"], [
        {"concept": "acos", "value": .50, "baseline": .35},
        {"concept": "ctr", "value": .01, "baseline": .02},
        {"concept": "impressions_growth", "value": .25},
    ]))
    assert result.outcome == Outcome.CONFLICT
    assert result.winner_rule == "R1"
    assert result.suppressed_rules[0]["rule_id"] == "R2"


def test_three_way_match_does_not_invent_precedence(engine):
    result = engine.evaluate(request(["R1", "R2", "R6"], [
        {"concept": "acos", "value": .50, "baseline": .35},
        {"concept": "ctr", "value": .01, "baseline": .02},
        {"concept": "impressions_growth", "value": .25},
        {"concept": "cvr", "value": .04, "baseline": .1},
    ]))
    assert result.outcome == Outcome.CONFLICT
    assert result.winner_rule is None
    assert result.action is None
    assert result.suppressed_rules == []
    assert result.disposition == Disposition.REVIEW


def test_r1_decimal_boundary_is_exact(engine):
    result = engine.evaluate(request(["R1"], [
        {"concept": "acos", "value": .455, "baseline": .35},
        {"concept": "ctr", "value": .01, "baseline": .02},
    ]))
    assert result.outcome == Outcome.NO_COVERAGE
    assert result.matched_rules == []


def test_candidate_claim_cannot_hide_another_matching_rule(engine):
    result = engine.evaluate(request(["R2"], [
        {"concept": "acos", "value": .50, "baseline": .35},
        {"concept": "ctr", "value": .01, "baseline": .02},
        {"concept": "impressions_growth", "value": .25},
    ]))
    assert result.outcome == Outcome.CONFLICT
    assert result.matched_rules == ["R2", "R1"]
    assert result.winner_rule == "R1"


def test_r7_is_recognized_but_never_executed(engine):
    result = engine.evaluate(request(["R7"], []))
    assert result.outcome == Outcome.NO_COVERAGE
    assert result.action is None
    assert result.disposition == Disposition.REVIEW


def test_client_gate_changes_r3_to_review(engine):
    result = engine.evaluate(request(
        ["R3"],
        [{"concept": "mta_roas", "value": 1.6, "baseline": 1.0}],
        client_id="demo_client_002",
    ))
    assert result.disposition == Disposition.REVIEW


@pytest.mark.parametrize(
    "extra_inputs",
    [[], [{"concept": "sp_min_daily_budget", "value": 1.0}]],
)
def test_r3_match_downgrades_without_concrete_g2_operands(engine, extra_inputs):
    result = engine.evaluate(request(
        ["R3"],
        [{"concept": "mta_roas", "value": 1.6, "baseline": 1.0}, *extra_inputs],
    ))
    assert result.outcome == Outcome.MATCH
    assert result.matched_rules == ["R3"]
    assert result.action.type == "budget_increase"
    assert result.disposition == Disposition.REVIEW
    g2 = next(item for item in result.guardrail_evaluations if item.guardrail_id == "G2")
    assert g2.applicable is False


@pytest.mark.parametrize(
    ("action", "inputs", "guardrail_id", "blocked"),
    [
        ({"type": "bid_adjustment", "param": {"new_bid": 1.8}}, [{"concept": "max_cpc", "value": 2.0}], "G1", False),
        ({"type": "bid_adjustment", "param": {"new_bid": 2.1}}, [{"concept": "max_cpc", "value": 2.0}], "G1", True),
        ({"type": "budget_decrease", "param": {"new_daily_budget": 1.5}}, [{"concept": "sp_min_daily_budget", "value": 1.0}], "G2", False),
        ({"type": "budget_decrease", "param": {"new_daily_budget": .9}}, [{"concept": "sp_min_daily_budget", "value": 1.0}], "G2", True),
    ],
)
def test_guardrails_are_generic(engine, action, inputs, guardrail_id, blocked):
    result = engine.evaluate(request([], inputs, proposed_action=action))
    evaluation = next(item for item in result.guardrail_evaluations if item.guardrail_id == guardrail_id)
    assert evaluation.passed is (not blocked)
    assert (result.disposition == Disposition.BLOCKED) is blocked


def test_proposed_action_cannot_replace_rule_action_or_gain_execution(engine):
    result = engine.evaluate(request(
        ["R3"],
        [{"concept": "mta_roas", "value": 1.6, "baseline": 1.0}],
        proposed_action={"type": "pause_campaign", "param": {}},
    ))
    assert result.outcome == Outcome.CONFLICT
    assert result.disposition == Disposition.REVIEW
    assert result.action.type == "budget_increase"

    uncovered = engine.evaluate(request([], [], proposed_action={"type": "unknown_action", "param": {}}))
    assert uncovered.outcome == Outcome.NO_COVERAGE
    assert uncovered.disposition == Disposition.REVIEW
    assert uncovered.action is None


def test_nested_non_finite_and_negative_baseline_are_rejected(engine):
    with pytest.raises(ValueError):
        request([], [], context={"nested": {"bad": float("inf")}})
    with pytest.raises(AppError) as baseline:
        engine.evaluate(request(["R3"], [{"concept": "mta_roas", "value": 1.0, "baseline": -1.0}]))
    assert baseline.value.code == "METRIC_OUT_OF_RANGE"


def test_unknown_rule_and_missing_baseline_fail_closed(engine):
    with pytest.raises(AppError) as unknown:
        engine.evaluate(request(["R99"], []))
    assert unknown.value.code == "UNKNOWN_RULE"
    with pytest.raises(AppError) as missing:
        engine.evaluate(request(["R3"], [{"concept": "mta_roas", "value": 2.0}]))
    assert missing.value.code == "MISSING_REQUIRED_METRIC"


def test_wrong_grain_out_of_range_and_non_finite_values_are_rejected(engine):
    with pytest.raises(AppError) as grain:
        engine.evaluate(request(
            ["R3"],
            [{"concept": "mta_roas", "value": 1.6, "baseline": 1.0}],
            entity={"grain": "campaign", "id": "wrong-grain"},
        ))
    assert grain.value.code == "ENTITY_GRAIN_MISMATCH"
    with pytest.raises(AppError) as metric_range:
        engine.evaluate(request(["R5"], [
            {"concept": "contribution_share", "value": -0.1},
            {"concept": "spend_share", "value": .25},
            {"concept": "attribution_divergence", "value": .03},
        ]))
    assert metric_range.value.code == "METRIC_OUT_OF_RANGE"
    with pytest.raises(ValueError):
        request(["R3"], [{"concept": "mta_roas", "value": float("nan"), "baseline": 1.0}])


def test_partially_supplied_guardrail_contract_fails_closed(engine):
    with pytest.raises(AppError) as missing:
        engine.evaluate(request([], [], proposed_action={"type": "bid_adjustment", "param": {"new_bid": 2.0}}))
    assert missing.value.code == "MISSING_REQUIRED_METRIC"


@pytest.mark.parametrize(
    ("action", "inputs"),
    [
        ({"type": "bid_adjustment", "param": {"new_bid": 1.0}}, [{"concept": "max_cpc", "value": True}]),
        ({"type": "bid_adjustment", "param": {"new_bid": "1.0"}}, [{"concept": "max_cpc", "value": 2.0}]),
    ],
)
def test_guardrail_operands_must_be_finite_numbers(engine, action, inputs):
    with pytest.raises(AppError) as invalid:
        engine.evaluate(request([], inputs, proposed_action=action, entity={"grain": "ad_group", "id": "guard"}))
    assert invalid.value.code == "INVALID_METRIC_TYPE"


def test_canonical_positive_negative_and_boundary_fixtures(engine):
    suite = json.loads((ONTOLOGY_ROOT / "assertions" / "story_assertions.json").read_text(encoding="utf-8"))
    scenarios = [item for item in suite["scenarios"] if item["category"] in {"rule_positive", "rule_negative", "rule_boundary"}]
    for scenario in scenarios:
        result = engine.evaluate(request(
            scenario["rule_refs"],
            scenario["inputs"],
            client_id=scenario["client_id"],
            entity=scenario["entity"],
        ))
        assert result.matched_rules == scenario["expected"].get("triggered_rules", []), scenario["assertion_id"]
        expected_disposition = scenario["expected"]["disposition"]
        if scenario["expected"].get("triggered_rules") == ["R3"]:
            expected_disposition = "REVIEW"
        assert result.disposition.value == expected_disposition, scenario["assertion_id"]
