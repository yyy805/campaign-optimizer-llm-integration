from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from campaign_optimizer.llm.agent_workflow_v6 import load_role_configuration
from campaign_optimizer.llm.qwen_client import QwenResponse, QwenUsage
from campaign_optimizer.llm.three_role_runner import RoleCallAdapter
from campaign_optimizer.llm.three_role_runner_v6 import ThreeRoleRunnerV6

ROOT = Path(__file__).parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "plan_a"


def fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def response(value) -> QwenResponse:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return QwenResponse(text, "request_mock", "response_mock", "mock", QwenUsage(total_tokens=7), "stop", 1.0)


class CapturingExecutor:
    def __init__(self, outputs, payloads):
        self.outputs = outputs
        self.payloads = payloads
        self.index = 0

    def chat(self, messages, *, parameters=None):
        self.payloads.append(json.loads(messages[1]["content"]))
        value = self.outputs[min(self.index, len(self.outputs) - 1)]
        self.index += 1
        return response(value)


def run_invalid(outputs):
    config = load_role_configuration()
    payloads = []
    client = CapturingExecutor(outputs, payloads)
    adapter = RoleCallAdapter(config, client_factory=lambda role, model: client)
    runner = ThreeRoleRunnerV6(configuration=config, role_calls=adapter)
    result = runner.run(
        request=fixture("llm_request.demo.json"),
        plan=fixture("final_plan.demo.json"),
        review=fixture("ontology_review.demo.json"),
        context=fixture("llm_context.demo.json"),
        revision_profile="baseline",
        dry_run=False,
    )
    return result, payloads


def invalid_candidate(category: str, sentinel: str):
    candidate = copy.deepcopy(fixture("llm_workflow_output.demo.json"))
    if category == "GUARD":
        candidate["retry_count"] = 1
    elif category == "SCHEMA":
        candidate["secret_extra_property"] = sentinel
    elif category == "SOURCE_BINDING":
        candidate["claims"][0]["value"] = sentinel
    elif category == "EXCHANGE":
        candidate["workflow_version"] = "wrong-version"
    else:
        raise AssertionError(category)
    return candidate


def test_v6_configuration_pins_executor_v3_without_mutating_v5():
    config = load_role_configuration()
    assert config.prompt_versions["executor"] == "executor_v3"
    v5 = json.loads((ROOT / "campaign_optimizer" / "llm" / "agent_roles.v5.json").read_text(encoding="utf-8"))
    assert v5["prompt_artifacts"]["executor"] == "executor_v2.md"


@pytest.mark.parametrize("category", ["GUARD", "SCHEMA", "SOURCE_BINDING", "EXCHANGE"])
def test_executor_failure_has_stable_safe_category_and_path(category: str):
    sentinel = "PRIVATE_CANDIDATE_VALUE_DO_NOT_LOG"
    invalid = invalid_candidate(category, sentinel)
    result, payloads = run_invalid([invalid, invalid])
    assert result.status == "FALLBACK"
    assert result.provider_calls == 2
    assert result.calls[0].error_code == f"EXECUTOR_{category}:" + (
        "output.retry_count" if category == "GUARD" else
        "output.schema" if category == "SCHEMA" else
        "output.claims" if category == "SOURCE_BINDING" else
        "output.exchange"
    )
    assert result.calls[1].error_code.startswith(f"EXECUTOR_REPAIR_{category}:")
    assert payloads[1]["server_format_repair"]["validation_category"] == category
    serialized = json.dumps({"calls": [item.__dict__ for item in result.calls], "fallback_reason": result.fallback_reason, "output": result.output}, ensure_ascii=False)
    assert sentinel not in serialized
    assert sentinel not in json.dumps(payloads[1], ensure_ascii=False)


def test_json_failure_is_bounded_and_never_echoed_to_repair_or_audit():
    sentinel = "DASHSCOPE_API_KEY=secret-value"
    result, payloads = run_invalid([f"not-json {sentinel}", f"still-not-json {sentinel}"])
    assert result.status == "FALLBACK"
    assert result.provider_calls == 2
    assert [item.error_code for item in result.calls] == [
        "EXECUTOR_JSON:output",
        "EXECUTOR_REPAIR_JSON:output",
    ]
    serialized = json.dumps({"calls": [item.__dict__ for item in result.calls], "fallback_reason": result.fallback_reason, "output": result.output, "repair_payload": payloads[1]}, ensure_ascii=False)
    assert sentinel not in serialized
    assert "secret-value" not in serialized


def test_non_sensitive_repair_payload_contains_only_category_path_and_instruction():
    invalid = invalid_candidate("SCHEMA", "PRIVATE")
    _, payloads = run_invalid([invalid, invalid])
    assert set(payloads[1]["server_format_repair"]) == {
        "validation_category",
        "validation_path",
        "instruction",
    }
