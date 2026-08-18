"""L1验收：方案A四份接口Schema、Golden Fixture和跨对象语义约束。"""
from __future__ import annotations

import copy
import json
from datetime import date
from pathlib import Path

import jsonschema
import pytest
from jsonschema import Draft7Validator, FormatChecker
from referencing import Registry, Resource

from campaign_optimizer.contracts.validation import validate_contract_bundle

ROOT = Path(__file__).parent.parent
SCHEMAS_DIR = ROOT / "campaign_optimizer" / "schemas"
FIXTURES_DIR = ROOT / "tests" / "fixtures" / "plan_a"
R5_PATH = (
    ROOT / 'campaign_optimizer' / 'ontology' / 'history' / 'rules'
    / 'R5.touchpoint.1.3-contract-hardening.json'
)

SCHEMA_FILES = {
    "final_plan": "final_plan.schema.json",
    "ontology_review": "ontology_review.schema.json",
    "llm_context": "llm_context.schema.json",
    "llm_workflow_output": "llm_workflow_output.schema.json",
}
FIXTURE_FILES = {
    "final_plan": "final_plan.demo.json",
    "ontology_review": "ontology_review.demo.json",
    "llm_context": "llm_context.demo.json",
    "llm_workflow_output": "llm_workflow_output.demo.json",
}
VERDICT_PRIORITY = {
    "SUPPORT": 0,
    "UNVERIFIED": 1,
    "INSUFFICIENT_EVIDENCE": 2,
    "CONFLICT": 3,
}


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def schemas() -> dict[str, dict]:
    return {name: _load(SCHEMAS_DIR / filename) for name, filename in SCHEMA_FILES.items()}


@pytest.fixture(scope="module")
def registry(schemas: dict[str, dict]) -> Registry:
    return Registry().with_resources(
        (schema["$id"], Resource.from_contents(schema)) for schema in schemas.values()
    )


@pytest.fixture
def fixtures() -> dict[str, dict]:
    return {name: _load(FIXTURES_DIR / filename) for name, filename in FIXTURE_FILES.items()}


def _validate_schema(instance: dict, schema: dict, registry: Registry) -> None:
    Draft7Validator(
        schema,
        registry=registry,
        format_checker=FormatChecker(),
    ).validate(instance)


def _validate_bundle(
    plan: dict,
    review: dict,
    context: dict,
    output: dict,
) -> None:
    """测试与后端调用同一份正式语义Gate。"""
    validate_contract_bundle(plan, review, context, output)


def test_all_schemas_are_valid_draft7(schemas):
    for schema in schemas.values():
        Draft7Validator.check_schema(schema)


@pytest.mark.parametrize("name", list(SCHEMA_FILES))
def test_golden_fixtures_pass_schema(name, schemas, registry, fixtures):
    _validate_schema(fixtures[name], schemas[name], registry)


@pytest.mark.parametrize(
    "name,required_field",
    [
        ("final_plan", "plan_id"),
        ("ontology_review", "review_id"),
        ("llm_context", "context_id"),
        ("llm_workflow_output", "status"),
    ],
)
def test_schema_rejects_missing_required_field(
    name, required_field, schemas, registry, fixtures
):
    broken = copy.deepcopy(fixtures[name])
    del broken[required_field]
    with pytest.raises(jsonschema.ValidationError):
        _validate_schema(broken, schemas[name], registry)


@pytest.mark.parametrize(
    "name,field,bad_value",
    [
        ("final_plan", "items", {}),
        ("ontology_review", "items", {}),
        ("llm_context", "allowed_fact_ids", "decision_fact_001"),
        ("llm_workflow_output", "retry_count", "0"),
    ],
)
def test_schema_rejects_wrong_type(name, field, bad_value, schemas, registry, fixtures):
    broken = copy.deepcopy(fixtures[name])
    broken[field] = bad_value
    with pytest.raises(jsonschema.ValidationError):
        _validate_schema(broken, schemas[name], registry)


@pytest.mark.parametrize("name", list(SCHEMA_FILES))
def test_schema_rejects_unknown_top_level_field(name, schemas, registry, fixtures):
    broken = copy.deepcopy(fixtures[name])
    broken["not_a_real_field"] = "typo"
    with pytest.raises(jsonschema.ValidationError):
        _validate_schema(broken, schemas[name], registry)


def test_bundle_semantics_pass(fixtures):
    _validate_bundle(
        fixtures["final_plan"],
        fixtures["ontology_review"],
        fixtures["llm_context"],
        fixtures["llm_workflow_output"],
    )


def test_bundle_rejects_non_14_day_period(fixtures):
    plan = copy.deepcopy(fixtures["final_plan"])
    context = copy.deepcopy(fixtures["llm_context"])
    plan["period"]["end_date"] = "2026-08-15"
    context["plan_context"] = plan
    with pytest.raises(ValueError, match="14个自然日"):
        _validate_bundle(
            plan,
            fixtures["ontology_review"],
            context,
            fixtures["llm_workflow_output"],
        )


def test_bundle_rejects_unknown_review_fact(fixtures):
    review = copy.deepcopy(fixtures["ontology_review"])
    context = copy.deepcopy(fixtures["llm_context"])
    review["items"][0]["matched_fact_ids"].append("review_fact_999")
    context["review_context"] = review
    with pytest.raises(jsonschema.ValidationError, match="expected to be empty"):
        _validate_bundle(
            fixtures["final_plan"],
            review,
            context,
            fixtures["llm_workflow_output"],
        )


def test_bundle_rejects_expanded_whitelist(fixtures):
    context = copy.deepcopy(fixtures["llm_context"])
    context["allowed_rule_ids"].append("R99")
    with pytest.raises(ValueError, match="allowed_rule_ids"):
        _validate_bundle(
            fixtures["final_plan"],
            fixtures["ontology_review"],
            context,
            fixtures["llm_workflow_output"],
        )


def test_bundle_rejects_wrong_overall_verdict(fixtures):
    review = copy.deepcopy(fixtures["ontology_review"])
    context = copy.deepcopy(fixtures["llm_context"])
    review["overall_verdict"] = "SUPPORT"
    context["review_context"] = review
    with pytest.raises(ValueError, match="overall_verdict"):
        _validate_bundle(
            fixtures["final_plan"],
            review,
            context,
            fixtures["llm_workflow_output"],
        )


def test_bundle_rejects_claim_value_tampering(fixtures):
    output = copy.deepcopy(fixtures["llm_workflow_output"])
    output["claims"][1]["value"] = 15
    with pytest.raises(ValueError, match="篡改了结构化事实"):
        _validate_bundle(
            fixtures["final_plan"],
            fixtures["ontology_review"],
            fixtures["llm_context"],
            output,
        )


def test_pending_fixture_does_not_claim_the_immutable_historical_rule(fixtures):
    r5 = _load(R5_PATH)
    review_item = fixtures["ontology_review"]["items"][0]
    assert review_item["rule_id"] is None
    assert review_item["rule_version"] is None
    assert review_item["verdict"] == "UNVERIFIED"
    assert r5["version_history"][-1]["version"] == "1.3-contract-hardening"
    assert r5["status"] == "ACTIVE"


def test_expected_explanation_contract_covers_fixed_fidelity_risks():
    expected = (FIXTURES_DIR / "initial_explanation.expected.md").read_text(
        encoding="utf-8"
    )
    for required in [
        "Sponsored Products",
        "增加10%",
        "CONFLICT",
        "1.3-contract-hardening",
        "Demo占位阈值",
        "不能还原小模型内部",
    ]:
        assert required in expected
    for forbidden_claim in [
        "把增加10%改成15%",
        "新增Sponsored Brands",
        "把增加预算说成减少预算",
    ]:
        assert forbidden_claim in expected
