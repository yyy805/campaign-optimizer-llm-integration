from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from campaign_optimizer.llm.agent_workflow_v8 import load_role_configuration
from campaign_optimizer.llm.qwen_client import QwenResponse, QwenUsage
from campaign_optimizer.llm.schema_diagnostic_guard_v8 import CLAIM_TYPE_ALLOWED_VALUES, LEGAL_CLAIM_TYPES
from campaign_optimizer.llm.three_role_runner_v7 import RoleCallAdapterV7
from campaign_optimizer.llm.three_role_runner_v8 import ThreeRoleRunnerV8

ROOT = Path(__file__).parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "plan_a"


def fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def response(value) -> QwenResponse:
    return QwenResponse(json.dumps(value, ensure_ascii=False), "request_mock", "response_mock", "mock", QwenUsage(total_tokens=7), "stop", 1.0)


class CapturingClient:
    def __init__(self, outputs):
        self.outputs = outputs
        self.index = 0
        self.payloads = []

    def chat(self, messages, *, parameters=None):
        self.payloads.append(json.loads(messages[1]["content"]))
        value = self.outputs[min(self.index, len(self.outputs) - 1)]
        self.index += 1
        return response(value)


def run_invalid(candidate):
    configuration = load_role_configuration()
    client = CapturingClient([candidate, candidate])
    adapter = RoleCallAdapterV7(configuration, client_factory=lambda role, model: client)
    result = ThreeRoleRunnerV8(configuration=configuration, role_calls=adapter).run(
        request=fixture("llm_request.demo.json"),
        plan=fixture("final_plan.demo.json"),
        review=fixture("ontology_review.demo.json"),
        context=fixture("llm_context.demo.json"),
        revision_profile="baseline",
        dry_run=False,
    )
    return result, client


def test_v8_prompt_hash_config_and_source_mapping_are_aligned():
    configuration = load_role_configuration()
    prompt_path = ROOT / "campaign_optimizer" / "llm" / "prompts" / "executor_v4.md"
    prompt = prompt_path.read_text(encoding="utf-8")
    assert configuration.roles.prompt_versions["executor"] == "executor_v4"
    assert configuration.roles.prompt_hashes["executor"] == hashlib.sha256(prompt_path.read_bytes()).hexdigest()
    for claim_type in LEGAL_CLAIM_TYPES:
        assert f"`{claim_type}`" in prompt
    assert "`PLAN_FIELD`: `source_id` is a `plan_item_*`" in prompt
    assert "`PLAN_PERIOD_FIELD`: `source_id` is the associated `plan_item_*`" in prompt
    assert "`REVIEW_FIELD`: `source_id` is a `review_item_*`" in prompt
    assert "`FACT_VALUE`: `source_id` is a `decision_fact_*` or `review_fact_*`" in prompt
    assert "`RULE_FIELD`: `source_id` is an allowed rule ID" in prompt
    assert "`type`,\n  `start_date`, or `end_date`" in prompt


def test_enum_repair_uses_only_exact_schema_constants_and_never_rejected_value():
    sentinel = "SECRET_REJECTED_CLAIM_TYPE"
    candidate = copy.deepcopy(fixture("llm_workflow_output.demo.json"))
    candidate["claims"][0]["claim_type"] = sentinel
    result, client = run_invalid(candidate)
    assert result.status == "FALLBACK"
    assert result.provider_calls == 2
    assert result.reserved_provider_calls == 3
    assert [call.error_code for call in result.calls] == [
        "EXECUTOR_SCHEMA.enum:output.claims[0].claim_type",
        "EXECUTOR_REPAIR_SCHEMA.enum:output.claims[0].claim_type",
    ]
    repair = client.payloads[1]["server_format_repair"]
    assert repair["allowed_values"] == list(CLAIM_TYPE_ALLOWED_VALUES)
    assert tuple(repair["allowed_values"]) == CLAIM_TYPE_ALLOWED_VALUES
    assert set(repair["allowed_values"]) == set(LEGAL_CLAIM_TYPES)
    serialized = json.dumps({"calls": [call.__dict__ for call in result.calls], "fallback_reason": result.fallback_reason, "output": result.output, "repair": repair}, ensure_ascii=False)
    assert sentinel not in serialized


def test_non_enum_repair_does_not_receive_unrelated_allowed_values():
    candidate = copy.deepcopy(fixture("llm_workflow_output.demo.json"))
    candidate["claims"] = "SECRET_WRONG_TYPE_VALUE"
    result, client = run_invalid(candidate)
    assert result.provider_calls == 2
    repair = client.payloads[1]["server_format_repair"]
    assert repair["validator"] == "type"
    assert repair["validation_path"] == "output.claims"
    assert "allowed_values" not in repair
    assert "SECRET_WRONG_TYPE_VALUE" not in json.dumps(repair, ensure_ascii=False)
