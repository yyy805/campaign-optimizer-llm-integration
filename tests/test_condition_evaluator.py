from campaign_optimizer.ontology.condition_evaluator import (
    EvaluationStatus,
    evaluate_condition,
    missing_required_concepts,
)


def _fact(name, value, **extra):
    return {"fact_id": f"review_fact_{name}", "name": name, "value": value, **extra}


def test_all_condition_matches_and_emits_leaf_trace():
    result = evaluate_condition(
        {"all": [
            {"concept": "a", "op": ">", "ref": 1},
            {"concept": "b", "op": "<=", "ref": 3},
        ]},
        {"a": _fact("a", 2), "b": _fact("b", 3)},
    )
    assert result.status == EvaluationStatus.MATCHED
    assert [entry["status"] for entry in result.trace] == ["MATCHED", "MATCHED"]


def test_all_false_is_not_matched_even_if_another_leaf_is_missing():
    result = evaluate_condition(
        {"all": [
            {"concept": "a", "op": ">", "ref": 10},
            {"concept": "b", "op": ">", "ref": 0},
        ]},
        {"a": _fact("a", 2)},
    )
    assert result.status == EvaluationStatus.NOT_MATCHED
    assert result.missing_evidence == ("b",)


def test_any_true_is_matched_even_if_another_leaf_is_missing():
    result = evaluate_condition(
        {"any": [
            {"concept": "a", "op": ">", "ref": 1},
            {"concept": "b", "op": ">", "ref": 0},
        ]},
        {"a": _fact("a", 2)},
    )
    assert result.status == EvaluationStatus.MATCHED


def test_baseline_reference_is_traced_and_missing_baseline_is_insufficient():
    condition = {"concept": "a", "op": ">", "ref": "baseline*1.5"}
    missing = evaluate_condition(condition, {"a": _fact("a", 4)})
    assert missing.status == EvaluationStatus.INSUFFICIENT
    assert missing.missing_rule_parameters == ("baseline_a",)

    matched = evaluate_condition(
        condition, {"a": _fact("a", 4, baseline_value=2)}
    )
    assert matched.status == EvaluationStatus.MATCHED
    assert matched.trace[0]["reference"] == 3


def test_minimal_missing_concepts_respects_any_branches():
    condition = {"any": [
        {"concept": "a", "op": ">", "ref": 0},
        {"all": [
            {"concept": "b", "op": ">", "ref": 0},
            {"concept": "c", "op": ">", "ref": 0},
        ]},
    ]}
    assert missing_required_concepts(condition, {"a"}) == set()
    assert missing_required_concepts(condition, set()) == {"a"}
