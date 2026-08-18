"""L1补充Gate：拒答/兜底、日期、非法JSON和嵌套字段。"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import jsonschema
import pytest
from jsonschema import Draft7Validator, FormatChecker
from referencing import Registry, Resource

ROOT = Path(__file__).parent.parent
SCHEMAS_DIR = ROOT / "campaign_optimizer" / "schemas"
FIXTURES_DIR = ROOT / "tests" / "fixtures" / "plan_a"

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


def _validate(instance: dict, schema: dict, registry: Registry) -> None:
    Draft7Validator(
        schema,
        registry=registry,
        format_checker=FormatChecker(),
    ).validate(instance)


@pytest.mark.parametrize("name", list(SCHEMA_FILES))
def test_every_top_level_required_field_is_enforced(name, schemas, registry, fixtures):
    for required_field in schemas[name]["required"]:
        broken = copy.deepcopy(fixtures[name])
        del broken[required_field]
        with pytest.raises(jsonschema.ValidationError):
            _validate(broken, schemas[name], registry)


@pytest.mark.parametrize(
    "name,mutator",
    [
        ("final_plan", lambda value: value["items"][0].update(extra="typo")),
        ("ontology_review", lambda value: value["items"][0].update(extra="typo")),
        (
            "llm_context",
                lambda value: value["plan_context"]["items"][0].update(
                runtime_confidence=0.9
            ),
        ),
        (
            "llm_workflow_output",
            lambda value: value["claims"][0].update(extra="typo"),
        ),
    ],
)
def test_schema_rejects_unknown_nested_field(
    name, mutator, schemas, registry, fixtures
):
    broken = copy.deepcopy(fixtures[name])
    mutator(broken)
    with pytest.raises(jsonschema.ValidationError):
        _validate(broken, schemas[name], registry)


@pytest.mark.parametrize(
    "name,mutator",
    [
        (
            "final_plan",
            lambda value: value["period"].update(start_date="not-a-date"),
        ),
        (
            "llm_context",
            lambda value: value.update(context_created_at="not-a-date-time"),
        ),
    ],
)
def test_format_checker_rejects_invalid_dates(
    name, mutator, schemas, registry, fixtures
):
    broken = copy.deepcopy(fixtures[name])
    mutator(broken)
    with pytest.raises(jsonschema.ValidationError):
        _validate(broken, schemas[name], registry)


@pytest.mark.parametrize(
    "invalid_json",
    [
        '{"schema_version": "1.0",}',
        "{'schema_version': '1.0'}",
        '{"schema_version": "1.0"',
    ],
)
def test_invalid_json_is_rejected_before_schema_validation(invalid_json):
    with pytest.raises(json.JSONDecodeError):
        json.loads(invalid_json)


def test_refused_workflow_output_passes_schema(schemas, registry, fixtures):
    refused = copy.deepcopy(fixtures["llm_workflow_output"])
    refused.update(
        {
            "status": "REFUSED",
            "intent": "OUT_OF_SCOPE",
            "answer": "当前功能只能解释本次方案和本体评价。",
            "claims": [],
            "facts_used": [],
            "rule_ids_used": [],
            "plan_item_ids_used": [],
            "limitations_included": False,
            "retry_count": 0,
            "fallback_used": False,
        }
    )
    _validate(refused, schemas["llm_workflow_output"], registry)


def test_fallback_workflow_output_passes_schema(schemas, registry, fixtures):
    fallback = copy.deepcopy(fixtures["llm_workflow_output"])
    fallback.update(
        {
            "status": "FALLBACK",
            "intent": "SYSTEM_FALLBACK",
            "answer": "解释服务暂时不可用，请稍后重试。",
            "claims": [],
            "facts_used": [],
            "rule_ids_used": [],
            "plan_item_ids_used": [],
            "limitations_included": False,
            "retry_count": 1,
            "fallback_used": True,
        }
    )
    _validate(fallback, schemas["llm_workflow_output"], registry)


def test_refused_output_cannot_carry_facts(schemas, registry, fixtures):
    refused = copy.deepcopy(fixtures["llm_workflow_output"])
    refused.update(
        {
            "status": "REFUSED",
            "intent": "OUT_OF_SCOPE",
            "answer": "当前问题不在支持范围内。",
            "claims": [],
            "limitations_included": False,
            "retry_count": 0,
            "fallback_used": False,
        }
    )
    with pytest.raises(jsonschema.ValidationError):
        _validate(refused, schemas["llm_workflow_output"], registry)


def test_golden_answer_preserves_fixed_plan_and_review(fixtures):
    answer = fixtures["llm_workflow_output"]["answer"]
    for required in ["10%", "UNVERIFIED"]:
        assert required in answer
    for forbidden in ["增加15%", "Sponsored Brands", "本体支持", "本体冲突"]:
        assert forbidden not in answer
