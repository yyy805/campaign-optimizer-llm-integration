from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from campaign_optimizer.llm.agent_workflow_v7 import load_role_configuration
from campaign_optimizer.llm.qwen_client import QwenResponse, QwenUsage
from campaign_optimizer.llm.three_role_runner_v7 import RoleCallAdapterV7, ThreeRoleRunnerV7

ROOT = Path(__file__).parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "plan_a"


def fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def response(value) -> QwenResponse:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return QwenResponse(text, "request_mock", "response_mock", "mock", QwenUsage(total_tokens=7), "stop", 1.0)


class CapturingClient:
    def __init__(self, outputs):
        self.outputs = outputs
        self.index = 0
        self.payloads = []
        self.parameters = []

    def chat(self, messages, *, parameters=None):
        self.payloads.append(json.loads(messages[1]["content"]))
        self.parameters.append(dict(parameters or {}))
        value = self.outputs[min(self.index, len(self.outputs) - 1)]
        self.index += 1
        return response(value)


def run_invalid(outputs):
    configuration = load_role_configuration()
    client = CapturingClient(outputs)
    adapter = RoleCallAdapterV7(configuration, client_factory=lambda role, model: client)
    runner = ThreeRoleRunnerV7(configuration=configuration, role_calls=adapter)
    result = runner.run(
        request=fixture("llm_request.demo.json"),
        plan=fixture("final_plan.demo.json"),
        review=fixture("ontology_review.demo.json"),
        context=fixture("llm_context.demo.json"),
        revision_profile="baseline",
        dry_run=False,
    )
    return result, client


def candidate(case: str, secret: str):
    value = copy.deepcopy(fixture("llm_workflow_output.demo.json"))
    if case == "nested_required":
        value["claims"][0]["value"] = secret
        del value["claims"][0]["source_id"]
    elif case == "additional_properties":
        value["claims"][0][secret] = secret
    elif case == "wrong_type":
        value["claims"] = secret
    elif case == "wrong_const":
        value["schema_version"] = secret
    else:
        raise AssertionError(case)
    return value


def test_v7_reuses_pinned_executor_v3_and_preserves_v6_config():
    configuration = load_role_configuration()
    assert configuration.roles.prompt_versions["executor"] == "executor_v3"
    assert configuration.executor_max_output_tokens == 4096
    v6 = json.loads((ROOT / "campaign_optimizer" / "llm" / "agent_roles.v6.json").read_text(encoding="utf-8"))
    assert v6["configuration_version"] == "agent_roles_v6"
    assert "generation_limits" not in v6


@pytest.mark.parametrize(
    ("case", "validator", "path"),
    [
        ("nested_required", "required", "output.claims[0].source_id"),
        ("additional_properties", "additionalProperties", "output.claims[0].*"),
        ("wrong_type", "type", "output.claims"),
        ("wrong_const", "const", "output.schema_version"),
    ],
)
def test_schema_diagnostic_is_exact_bounded_and_secret_free(case, validator, path):
    secret = "DASHSCOPE_API_KEY_secret_candidate_value"
    invalid = candidate(case, secret)
    result, client = run_invalid([invalid, invalid])
    expected_initial = f"EXECUTOR_SCHEMA.{validator}:{path}"
    expected_repair = f"EXECUTOR_REPAIR_SCHEMA.{validator}:{path}"
    assert result.status == "FALLBACK"
    assert result.provider_calls == 2
    assert result.reserved_provider_calls == 3
    assert [call.error_code for call in result.calls] == [expected_initial, expected_repair]
    repair = client.payloads[1]["server_format_repair"]
    assert repair == {
        "validation_category": "SCHEMA",
        "validation_path": path,
        "validator": validator,
        "instruction": "Return a complete corrected JSON object under the pinned contract.",
    }
    serialized = json.dumps({"calls": [call.__dict__ for call in result.calls], "fallback_reason": result.fallback_reason, "output": result.output, "repair": repair}, ensure_ascii=False)
    assert secret not in serialized
    assert "secret_candidate_value" not in serialized


def test_executor_only_receives_max_output_tokens():
    configuration = load_role_configuration()
    clients = {}

    def factory(role, model):
        client = CapturingClient([{}])
        clients[role] = client
        return client

    adapter = RoleCallAdapterV7(configuration, client_factory=factory)
    for role in ("triage", "executor", "reviewer"):
        adapter.call_json(role=role, payload={})
    assert "max_tokens" not in clients["triage"].parameters[0]
    assert clients["executor"].parameters[0]["max_tokens"] == 4096
    assert "max_tokens" not in clients["reviewer"].parameters[0]


def test_json_failure_still_gets_exactly_one_safe_repair():
    secret = "raw-secret-must-not-echo"
    result, client = run_invalid([f"bad-json {secret}", f"bad-again {secret}"])
    assert result.provider_calls == 2
    assert [call.error_code for call in result.calls] == [
        "EXECUTOR_JSON.parse:output",
        "EXECUTOR_REPAIR_JSON.parse:output",
    ]
    assert secret not in json.dumps(client.payloads[1], ensure_ascii=False)
