from __future__ import annotations

import copy
import json
from pathlib import Path

from campaign_optimizer.llm.agent_workflow_v9 import REVIEWER_REVERT_MODEL, TEMPORARY_MODELS, load_role_configuration
from campaign_optimizer.llm.qwen_client import QwenResponse, QwenUsage
from campaign_optimizer.llm.schema_diagnostic_guard_v8 import CLAIM_TYPE_ALLOWED_VALUES
from campaign_optimizer.llm.three_role_runner_v7 import RoleCallAdapterV7
from campaign_optimizer.llm.three_role_runner_v9 import ThreeRoleRunnerV9

ROOT = Path(__file__).parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "plan_a"


def fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def response(value) -> QwenResponse:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return QwenResponse(text, "request_mock", "response_mock", "mock", QwenUsage(total_tokens=7), "stop", 1.0)


class CapturingClient:
    def __init__(self, outputs=None):
        self.outputs = outputs or [{}]
        self.index = 0
        self.payloads = []
        self.parameters = []

    def chat(self, messages, *, parameters=None):
        self.payloads.append(json.loads(messages[1]["content"]))
        self.parameters.append(dict(parameters or {}))
        value = self.outputs[min(self.index, len(self.outputs) - 1)]
        self.index += 1
        return response(value)


def test_temporary_mapping_is_explicit_and_reversible():
    configuration = load_role_configuration()
    assert dict(configuration.roles.model_aliases) == TEMPORARY_MODELS
    assert configuration.reviewer_revert_model == REVIEWER_REVERT_MODEL == "qwen3.8-max-preview"
    assert configuration.roles.prompt_versions == {
        "triage": "triage_v2",
        "executor": "executor_v4",
        "reviewer": "reviewer_v3",
    }


def test_adapter_sends_exact_api_model_ids_and_executor_only_limit():
    configuration = load_role_configuration()
    seen_models = []
    clients = {}

    def factory(role, model):
        seen_models.append((role, model))
        client = CapturingClient()
        clients[role] = client
        return client

    adapter = RoleCallAdapterV7(configuration, client_factory=factory)
    for role in ("triage", "executor", "reviewer"):
        adapter.call_json(role=role, payload={})
    assert seen_models == [
        ("triage", "qwen3.6-flash"),
        ("executor", "qwen3.7-max"),
        ("reviewer", "qwen3.7-plus"),
    ]
    assert "max_tokens" not in clients["triage"].parameters[0]
    assert clients["executor"].parameters[0]["max_tokens"] == 4096
    assert "max_tokens" not in clients["reviewer"].parameters[0]


def test_v9_preserves_v8_enum_repair_and_exactly_one_repair():
    sentinel = "REJECTED_SECRET_CLAIM_TYPE"
    candidate = copy.deepcopy(fixture("llm_workflow_output.demo.json"))
    candidate["claims"][0]["claim_type"] = sentinel
    configuration = load_role_configuration()
    client = CapturingClient([candidate, candidate])
    adapter = RoleCallAdapterV7(configuration, client_factory=lambda role, model: client)
    result = ThreeRoleRunnerV9(configuration=configuration, role_calls=adapter).run(
        request=fixture("llm_request.demo.json"),
        plan=fixture("final_plan.demo.json"),
        review=fixture("ontology_review.demo.json"),
        context=fixture("llm_context.demo.json"),
        revision_profile="baseline",
        dry_run=False,
    )
    assert result.status == "FALLBACK"
    assert result.provider_calls == 2
    assert result.reserved_provider_calls == 3
    assert len(result.calls) == 2
    repair = client.payloads[1]["server_format_repair"]
    assert repair["allowed_values"] == list(CLAIM_TYPE_ALLOWED_VALUES)
    assert sentinel not in json.dumps({"repair": repair, "calls": [call.__dict__ for call in result.calls], "output": result.output}, ensure_ascii=False)


def test_dry_run_records_temporary_executor_and_reviewer_ids():
    result = ThreeRoleRunnerV9().run(
        request=fixture("llm_request.demo.json"),
        plan=fixture("final_plan.demo.json"),
        review=fixture("ontology_review.demo.json"),
        context=fixture("llm_context.demo.json"),
        revision_profile="baseline",
        dry_run=True,
    )
    assert result.provider_calls == 0
    assert [(call.role, call.model) for call in result.calls] == [
        ("executor", "qwen3.7-max"),
        ("reviewer", "qwen3.7-plus"),
        ("executor", "qwen3.7-max"),
    ]
