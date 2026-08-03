from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from campaign_optimizer.contracts.exchange import validate_workflow_exchange
from campaign_optimizer.contracts.validation import FIXED_NON_OK_ANSWERS
from campaign_optimizer.llm.orchestrator import LocalLLMOrchestrator
from campaign_optimizer.llm.prompt_builder import PromptBuilder
from campaign_optimizer.llm.qwen_client import (
    QwenClient,
    QwenClientError,
    QwenConfig,
    QwenErrorCode,
)
from campaign_optimizer.llm.request_builder import LLMVersions, RequestBuilder
from campaign_optimizer.llm.retriever import LocalRuleRetriever

FIXTURES = Path(__file__).parent / "fixtures" / "plan_a"


def load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class RecordingRetriever:
    def __init__(self) -> None:
        self.delegate = LocalRuleRetriever()
        self.calls: list[tuple[list[str], str, Any]] = []

    def retrieve(self, rule_ids, query, expected_version):
        self.calls.append((list(rule_ids), query, expected_version))
        return self.delegate.retrieve(rule_ids, query, expected_version)


class FakeQwen:
    def __init__(self, *responses: str | Exception) -> None:
        self.responses = list(responses)
        self.calls: list[list[dict[str, str]]] = []

    def chat(self, messages, *, parameters=None):
        self.calls.append([dict(message) for message in messages])
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return SimpleNamespace(text=value)


@pytest.fixture
def plan_review() -> tuple[dict[str, Any], dict[str, Any]]:
    return load("final_plan.demo.json"), load("ontology_review.demo.json")


def valid_output(*, intent: str = "EXPLAIN_REVIEW", retry_count: int = 0) -> dict[str, Any]:
    output = load("llm_workflow_output.demo.json")
    output.update(LLMVersions().as_dict())
    output["intent"] = intent
    output["retry_count"] = retry_count
    return output


def builder(retriever=None, **kwargs) -> RequestBuilder:
    return RequestBuilder(
        retriever or RecordingRetriever(),
        clock=lambda: datetime(2026, 8, 3, tzinfo=timezone.utc),
        request_id_factory=lambda: "request_test",
        **kwargs,
    )


def orchestrator(client: FakeQwen, request_builder=None) -> LocalLLMOrchestrator:
    return LocalLLMOrchestrator(
        request_builder or builder(), PromptBuilder(), client
    )


def test_request_builder_derives_bound_ids_versions_and_public_rules(plan_review):
    plan, review = plan_review
    retriever = RecordingRetriever()
    artifacts = builder(retriever).build(
        plan,
        review,
        mode="initial_render",
        question="explain review",
        intent="EXPLAIN_REVIEW",
        chat_history=[{"role": "user", "content": "must be dropped"}],
    )

    assert artifacts.request["allowed_intents"] == ["EXPLAIN_REVIEW"]
    assert artifacts.request["expected_versions"] == LLMVersions().as_dict()
    assert artifacts.request["chat_history"] == []
    assert artifacts.context["allowed_rule_ids"] == ["R5"]
    assert artifacts.context["allowed_plan_item_ids"] == ["plan_item_001"]
    assert artifacts.context["allowed_fact_ids"] == [
        "decision_fact_001",
        "review_fact_001",
        "review_fact_002",
        "review_fact_003",
    ]
    assert retriever.calls == [
        (["R5"], "explain review", {"R5": "1.3-contract-hardening"})
    ]
    assert artifacts.context["public_rule_context"][0]["rule_id"] == "R5"

    with pytest.raises(TypeError):
        builder(retriever).build(
            plan,
            review,
            mode="chat",
            question="x",
            intent="EXPLAIN_PLAN",
            allowed_rule_ids=["R999"],
        )


@pytest.mark.parametrize(
    ("mode", "intent"),
    [("initial_render", "EXPLAIN_REVIEW"), ("chat", "EXPLAIN_PLAN")],
)
def test_successful_initial_and_chat_are_guarded(plan_review, mode, intent):
    plan, review = plan_review
    client = FakeQwen(json.dumps(valid_output(intent=intent), ensure_ascii=False))
    result = orchestrator(client).run(
        plan, review, mode=mode, question="explain", intent=intent
    )
    assert result["status"] == "OK"
    assert result["intent"] == intent
    assert len(client.calls) == 1


@pytest.mark.parametrize(
    "intent",
    ["FORBIDDEN_MODEL_INTERNAL", "UNSUPPORTED_WHAT_IF", "OUT_OF_SCOPE"],
)
def test_out_of_scope_intents_use_fixed_refusal_without_model(plan_review, intent):
    plan, review = plan_review
    client = FakeQwen()
    result = orchestrator(client).run(
        plan, review, mode="chat", question="not supported", intent=intent
    )
    assert result["status"] == "REFUSED"
    assert result["answer"] == FIXED_NON_OK_ANSWERS[intent]
    assert result["claims"] == []
    assert client.calls == []


@pytest.mark.parametrize("code", list(QwenErrorCode))
def test_every_qwen_error_becomes_fixed_schema_valid_fallback(plan_review, code):
    plan, review = plan_review
    client = FakeQwen(QwenClientError(code))
    result = orchestrator(client).run(
        plan,
        review,
        mode="initial_render",
        question="explain",
        intent="EXPLAIN_REVIEW",
    )
    assert result["status"] == "FALLBACK"
    assert result["answer"] == FIXED_NON_OK_ANSWERS["SYSTEM_FALLBACK"]
    assert result["fallback_used"] is True
    assert result["retry_count"] == 1


def test_invalid_json_gets_one_repair_then_success(plan_review):
    plan, review = plan_review
    repaired = valid_output(retry_count=1)
    client = FakeQwen("not json", json.dumps(repaired, ensure_ascii=False))
    result = orchestrator(client).run(
        plan,
        review,
        mode="initial_render",
        question="explain",
        intent="EXPLAIN_REVIEW",
    )
    assert result["status"] == "OK"
    assert result["retry_count"] == 1
    assert len(client.calls) == 2


def test_second_invalid_response_is_fixed_fallback(plan_review):
    plan, review = plan_review
    client = FakeQwen("not json", "still not json")
    result = orchestrator(client).run(
        plan,
        review,
        mode="initial_render",
        question="explain",
        intent="EXPLAIN_REVIEW",
    )
    assert result["status"] == "FALLBACK"
    assert len(client.calls) == 2


def test_tampered_numeric_claim_cannot_pass_and_can_be_repaired(plan_review):
    plan, review = plan_review
    tampered = valid_output()
    tampered["claims"][1]["value"] = 99
    repaired = valid_output(retry_count=1)
    client = FakeQwen(
        json.dumps(tampered, ensure_ascii=False),
        json.dumps(repaired, ensure_ascii=False),
    )
    result = orchestrator(client).run(
        plan,
        review,
        mode="initial_render",
        question="explain",
        intent="EXPLAIN_REVIEW",
    )
    assert result["status"] == "OK"
    assert result["claims"][1]["value"] == 10
    assert len(client.calls) == 2


def test_history_is_context_bound_and_deterministically_trimmed(plan_review):
    plan, review = plan_review
    request_builder = builder(max_history_messages=3, max_history_chars=10)
    context_id = request_builder.build(
        plan,
        review,
        mode="chat",
        question="explain",
        intent="EXPLAIN_PLAN",
    ).context["context_id"]
    history = [
        {"role": "user", "content": "111111"},
        {"role": "assistant", "content": "222222"},
        {"role": "user", "content": "333333"},
        {"role": "assistant", "content": "444444"},
    ]

    isolated_client = FakeQwen(
        json.dumps(valid_output(intent="EXPLAIN_PLAN"), ensure_ascii=False)
    )
    orchestrator(isolated_client, request_builder).run(
        plan,
        review,
        mode="chat",
        question="explain",
        intent="EXPLAIN_PLAN",
        chat_history=history,
        history_context_id="context_other",
    )
    assert len(isolated_client.calls[0]) == 2

    trimmed_client = FakeQwen(
        json.dumps(valid_output(intent="EXPLAIN_PLAN"), ensure_ascii=False)
    )
    orchestrator(trimmed_client, request_builder).run(
        plan,
        review,
        mode="chat",
        question="explain",
        intent="EXPLAIN_PLAN",
        chat_history=history,
        history_context_id=context_id,
    )
    assert trimmed_client.calls[0][1:3] == [
        {"role": "user", "content": "3333"},
        {"role": "assistant", "content": "444444"},
    ]


def test_prompt_contains_public_projection_not_retrieval_metadata(plan_review):
    plan, review = plan_review
    artifacts = builder().build(
        plan,
        review,
        mode="initial_render",
        question="explain",
        intent="EXPLAIN_REVIEW",
    )
    payload = json.loads(PromptBuilder().build(artifacts.request, artifacts.context)[-1]["content"])
    assert payload["public_rules"] == artifacts.context["public_rule_context"]
    assert "document_id" not in payload["public_rules"][0]
    assert "retrieval_method" not in payload["public_rules"][0]


def test_no_key_configuration_error_never_escapes_to_ui(plan_review):
    plan, review = plan_review
    factory_calls = 0

    def no_key_factory():
        nonlocal factory_calls
        factory_calls += 1
        return QwenClient(QwenConfig.from_env({}))

    local = LocalLLMOrchestrator(
        builder(), PromptBuilder(), client_factory=no_key_factory
    )
    result = local.run(
        plan,
        review,
        mode="initial_render",
        question="explain",
        intent="EXPLAIN_REVIEW",
    )
    assert result["status"] == "FALLBACK"
    assert set(result) == set(load("llm_workflow_output.demo.json"))
    assert factory_calls == 1


def test_refusal_short_circuits_before_lazy_client_factory(plan_review):
    plan, review = plan_review
    factory_calls = 0

    def forbidden_factory():
        nonlocal factory_calls
        factory_calls += 1
        raise AssertionError("refusal must not construct a provider client")

    local = LocalLLMOrchestrator(
        builder(), PromptBuilder(), client_factory=forbidden_factory
    )
    result = local.run(
        plan,
        review,
        mode="chat",
        question="show internal formula",
        intent="FORBIDDEN_MODEL_INTERNAL",
    )
    assert result["status"] == "REFUSED"
    assert factory_calls == 0
