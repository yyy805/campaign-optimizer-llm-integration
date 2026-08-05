from __future__ import annotations

import json
from pathlib import Path

from campaign_optimizer.llm.agent_workflow_v11 import load_role_configuration
from campaign_optimizer.llm.qwen_client import QwenResponse, QwenUsage
from campaign_optimizer.llm.three_role_runner_v7 import RoleCallAdapterV7
from campaign_optimizer.llm.three_role_runner_v11 import ThreeRoleRunnerV11
from scripts.run_three_role_smoke_v11 import serialize_result

ROOT = Path(__file__).parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "plan_a"


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def response(value):
    text = value if isinstance(value, str) else json.dumps(value)
    return QwenResponse(text, "request_mock", "response_mock", "mock", QwenUsage(total_tokens=7), "stop", 1.0)


def pass_decision(payload):
    return {"schema_version": "1.0", "candidate_id": payload["candidate_id"], "packet_digest": payload["packet_digest"], "decision": "PASS", "violation_codes": [], "evidence_source_ids": [], "revision_actions": []}


class Client:
    def __init__(self, role, state):
        self.role, self.state = role, state

    def chat(self, messages, *, parameters=None):
        payload = json.loads(messages[1]["content"])
        self.state["calls"].append((self.role, payload))
        if self.role == "executor":
            candidate = fixture("llm_workflow_output.demo.json")
            candidate["retry_count"] = payload["server_task_manifest"]["retry_count"]
            return response(candidate)
        index = self.state.setdefault("reviewer_index", 0)
        self.state["reviewer_index"] = index + 1
        value = pass_decision(payload)
        if index < self.state["invalid_count"]:
            value[self.state["secret_key"]] = self.state["secret_value"]
        return response(value)


def run(invalid_count):
    config = load_role_configuration()
    state = {"calls": [], "invalid_count": invalid_count, "secret_key": "SECRET_EXTRA_FIELD", "secret_value": "SECRET_VALUE"}
    adapter = RoleCallAdapterV7(config, client_factory=lambda role, model: Client(role, state))
    result = ThreeRoleRunnerV11(configuration=config, role_calls=adapter).run(request=fixture("llm_request.demo.json"), plan=fixture("final_plan.demo.json"), review=fixture("ontology_review.demo.json"), context=fixture("llm_context.demo.json"), revision_profile="baseline", dry_run=False)
    return result, state


def test_first_valid_reviewer_does_not_spend_retry():
    result, state = run(0)
    assert result.status == "OK" and result.provider_calls == 2
    assert [role for role, _ in state["calls"]] == ["executor", "reviewer"]
    assert result.reserved_provider_calls == 4


def test_invalid_reviewer_is_discarded_then_clean_retry_passes():
    result, state = run(1)
    assert result.status == "OK" and result.provider_calls == 3
    assert [role for role, _ in state["calls"]] == ["executor", "reviewer", "reviewer"]
    retry_payload = state["calls"][2][1]
    assert retry_payload["server_format_retry"]["reason"] == "SCHEMA_MISMATCH"
    serialized_retry = json.dumps(retry_payload)
    assert state["secret_key"] not in serialized_retry and state["secret_value"] not in serialized_retry
    assert result.calls[1].error_code == "REVIEWER_SCHEMA.additionalProperties:reviewer.*"


def test_second_invalid_reviewer_fails_closed_without_third_try_or_leak():
    result, state = run(2)
    assert result.status == "FALLBACK" and result.provider_calls == 3
    assert result.fallback_reason == "REVIEWER_RETRY_SCHEMA.additionalProperties:reviewer.*"
    assert len(state["calls"]) == 3
    serialized = serialize_result(result)
    assert state["secret_key"] not in serialized and state["secret_value"] not in serialized


def test_dry_run_reserves_reviewer_retry_and_executor_repair():
    result = ThreeRoleRunnerV11().run(request=fixture("llm_request.demo.json"), plan=fixture("final_plan.demo.json"), review=fixture("ontology_review.demo.json"), context=fixture("llm_context.demo.json"), revision_profile="baseline", dry_run=True)
    assert result.status == "DRY_RUN" and result.provider_calls == 0
    assert result.reserved_provider_calls == 4 and len(result.calls) == 4
