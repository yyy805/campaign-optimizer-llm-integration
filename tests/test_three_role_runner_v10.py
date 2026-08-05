from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from campaign_optimizer.llm.agent_workflow_v10 import load_role_configuration
from campaign_optimizer.llm.qwen_client import QwenResponse, QwenUsage
from campaign_optimizer.llm.reviewer_diagnostic_v10 import ReviewerDecisionFailure, validate_reviewer_schema
from campaign_optimizer.llm.three_role_runner_v7 import RoleCallAdapterV7
from campaign_optimizer.llm.three_role_runner_v10 import ThreeRoleRunnerV10
from scripts.run_three_role_smoke_v10 import serialize_result

ROOT = Path(__file__).parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "plan_a"
EXACT_KEYS = {
    "schema_version", "candidate_id", "packet_digest", "decision",
    "violation_codes", "evidence_source_ids", "revision_actions",
}


def fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def response(value) -> QwenResponse:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return QwenResponse(text, "request_mock", "response_mock", "mock", QwenUsage(total_tokens=7), "stop", 1.0)


def reviewer_decision(payload, mutate=None):
    decision = {
        "schema_version": "1.0",
        "candidate_id": payload["candidate_id"],
        "packet_digest": payload["packet_digest"],
        "decision": "PASS",
        "violation_codes": [],
        "evidence_source_ids": [],
        "revision_actions": [],
    }
    if mutate is not None:
        mutate(decision)
    return decision


class RoleClient:
    def __init__(self, role, reviewer_mutate, calls, reviewer_raw=None):
        self.role = role
        self.reviewer_mutate = reviewer_mutate
        self.reviewer_raw = reviewer_raw
        self.calls = calls

    def chat(self, messages, *, parameters=None):
        payload = json.loads(messages[1]["content"])
        self.calls.append((self.role, dict(parameters or {})))
        if self.role == "executor":
            candidate = fixture("llm_workflow_output.demo.json")
            candidate["retry_count"] = payload["server_task_manifest"]["retry_count"]
            return response(candidate)
        if self.reviewer_raw is not None:
            return response(self.reviewer_raw)
        return response(reviewer_decision(payload, self.reviewer_mutate))


def run(reviewer_mutate=None, *, reviewer_raw=None):
    configuration = load_role_configuration()
    calls = []
    adapter = RoleCallAdapterV7(
        configuration,
        client_factory=lambda role, model: RoleClient(role, reviewer_mutate, calls, reviewer_raw),
    )
    result = ThreeRoleRunnerV10(configuration=configuration, role_calls=adapter).run(
        request=fixture("llm_request.demo.json"),
        plan=fixture("final_plan.demo.json"),
        review=fixture("ontology_review.demo.json"),
        context=fixture("llm_context.demo.json"),
        revision_profile="baseline",
        dry_run=False,
    )
    return result, calls


def schema_decision(decision: str):
    value = {
        "schema_version": "1.0",
        "candidate_id": "candidate_x",
        "packet_digest": "a" * 64,
        "decision": decision,
        "violation_codes": [],
        "evidence_source_ids": [],
        "revision_actions": [],
    }
    if decision == "REVISE":
        value.update({
            "violation_codes": ["MISSING_LIMITATION"],
            "evidence_source_ids": ["review_item_001"],
            "revision_actions": [{"operation": "ADD_REQUIRED_LIMITATION", "target_claim_id": None, "source_id": "review_item_001"}],
        })
    elif decision == "REJECT":
        value["violation_codes"] = ["UNRESOLVABLE_CONFLICT"]
    return value


def test_reviewer_v4_prompt_hash_and_exact_output_rules_are_pinned():
    configuration = load_role_configuration()
    prompt_path = ROOT / "campaign_optimizer" / "llm" / "prompts" / "reviewer_v4.md"
    prompt = prompt_path.read_text(encoding="utf-8")
    assert configuration.roles.prompt_versions["reviewer"] == "reviewer_v4"
    assert configuration.roles.prompt_hashes["reviewer"] == hashlib.sha256(prompt_path.read_bytes()).hexdigest()
    for key in EXACT_KEYS:
        assert f"`{key}`" in prompt
    for forbidden in ("`audit`", "`corrections`", "reasoning", "explanations"):
        assert forbidden in prompt
    assert "`PASS`: `violation_codes`, `evidence_source_ids`, and `revision_actions` are\n  all empty arrays." in prompt
    assert "`REVISE`: all three arrays contain at least one schema-valid item" in prompt
    assert "`REJECT`: `violation_codes` contains at least one schema-valid item" in prompt


@pytest.mark.parametrize("field", ["violation_codes", "evidence_source_ids", "revision_actions"])
def test_pass_rejects_each_nonempty_array(field):
    value = schema_decision("PASS")
    value[field] = (
        ["MISSING_LIMITATION"] if field == "violation_codes" else
        ["review_item_001"] if field == "evidence_source_ids" else
        [{"operation": "ADD_REQUIRED_LIMITATION", "target_claim_id": None, "source_id": "review_item_001"}]
    )
    with pytest.raises(ReviewerDecisionFailure):
        validate_reviewer_schema(value)


@pytest.mark.parametrize("field", ["violation_codes", "evidence_source_ids", "revision_actions"])
@pytest.mark.parametrize("mode", ["missing", "empty"])
def test_revise_rejects_each_missing_or_empty_required_array(field, mode):
    value = schema_decision("REVISE")
    if mode == "missing":
        del value[field]
    else:
        value[field] = []
    with pytest.raises(ReviewerDecisionFailure):
        validate_reviewer_schema(value)


def test_reject_requires_violation_and_forbids_revision_actions():
    empty_violation = schema_decision("REJECT")
    empty_violation["violation_codes"] = []
    with pytest.raises(ReviewerDecisionFailure):
        validate_reviewer_schema(empty_violation)
    nonempty_revision = schema_decision("REJECT")
    nonempty_revision["revision_actions"] = [{"operation": "ADD_REQUIRED_LIMITATION", "target_claim_id": None, "source_id": "review_item_001"}]
    with pytest.raises(ReviewerDecisionFailure):
        validate_reviewer_schema(nonempty_revision)


def test_valid_exact_pass_reaches_ok_with_two_provider_calls():
    result, calls = run()
    assert result.status == "OK"
    assert result.provider_calls == 2
    assert result.reserved_provider_calls == 3
    assert [role for role, _ in calls] == ["executor", "reviewer"]


def test_reviewer_non_json_fails_closed_without_repair_or_raw_text():
    sentinel = "not-json DASHSCOPE_API_KEY=secret"
    result, calls = run(reviewer_raw=sentinel)
    assert result.status == "FALLBACK"
    assert result.provider_calls == 2
    assert len(calls) == 2
    assert result.fallback_reason == "REVIEWER_JSON.parse:reviewer"
    assert sentinel not in serialize_result(result)
    assert "secret" not in serialize_result(result)


def test_extra_reviewer_properties_fail_closed_without_names_values_or_repair():
    secret_key = "DASHSCOPE_API_KEY_SECRET_EXTRA_NAME"
    secret_value = "workspace-secret-value"

    def mutate(value):
        value[secret_key] = secret_value
        value["audit"] = secret_value
        value["corrections"] = [secret_value]

    result, calls = run(mutate)
    assert result.status == "FALLBACK"
    assert result.provider_calls == 2
    assert result.reserved_provider_calls == 3
    assert len(calls) == 2
    assert result.fallback_reason == "REVIEWER_SCHEMA.additionalProperties:reviewer.*"
    serialized = serialize_result(result)
    for forbidden in (secret_key, secret_value, "audit", "corrections", "DASHSCOPE_API_KEY", "workspace-secret", "You are the Campaign Optimizer REVIEWER"):
        assert forbidden not in serialized
    assert "properties" not in serialized and "reviewer_decision_v3" not in serialized


def test_nested_invalid_reviewer_value_has_sanitized_path_and_no_raw_value():
    sentinel = "SECRET_BAD_OPERATION"

    def mutate(value):
        value.update({
            "decision": "REVISE",
            "violation_codes": ["MISSING_LIMITATION"],
            "evidence_source_ids": ["review_item_001"],
            "revision_actions": [{"operation": sentinel, "target_claim_id": None, "source_id": "review_item_001"}],
        })

    result, _ = run(mutate)
    assert result.status == "FALLBACK"
    assert result.provider_calls == 2
    assert result.fallback_reason == "REVIEWER_SCHEMA.enum:reviewer.revision_actions[0].operation"
    assert sentinel not in serialize_result(result)


@pytest.mark.parametrize("binding", ["candidate_id", "packet_digest", "evidence_source_ids"])
def test_reviewer_binding_failures_do_not_escape_or_leak(binding):
    sentinel = f"SECRET_{binding}"

    def mutate(value):
        if binding == "candidate_id":
            value["candidate_id"] = f"candidate_{sentinel}"
        elif binding == "packet_digest":
            value["packet_digest"] = "b" * 64
        else:
            value.update({"decision": "REVISE", "violation_codes": ["MISSING_LIMITATION"], "evidence_source_ids": ["review_item_SECRET"], "revision_actions": [{"operation": "ADD_REQUIRED_LIMITATION", "target_claim_id": None, "source_id": "review_item_001"}]})

    result, calls = run(mutate)
    assert result.status == "FALLBACK"
    assert result.provider_calls == 2 and len(calls) == 2
    assert result.fallback_reason == "REVIEWER_BINDING.guard:reviewer.decision"
    serialized = serialize_result(result)
    assert sentinel not in serialized
    assert "review_item_SECRET" not in serialized
    assert "b" * 64 not in serialized


def test_schema_exception_metadata_is_safe_and_contains_no_schema_or_instance_dump():
    sentinel_key = "SECRET_EXTRA_KEY_NAME"
    sentinel_value = "SECRET_INSTANCE_VALUE"
    value = schema_decision("PASS")
    value[sentinel_key] = sentinel_value
    with pytest.raises(ReviewerDecisionFailure) as captured:
        validate_reviewer_schema(value)
    error = captured.value
    serialized = json.dumps({"category": error.category, "validator": error.validator, "path": error.path, "message": str(error)}, ensure_ascii=False)
    assert serialized == '{"category": "SCHEMA", "validator": "additionalProperties", "path": "reviewer.*", "message": "reviewer decision rejected"}'
    assert sentinel_key not in serialized and sentinel_value not in serialized
    assert "properties" not in serialized and "required" not in serialized
