from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from campaign_optimizer.contracts.validation import ContractValidationError
from campaign_optimizer.llm.intent_policy import HybridIntentPolicy, RouterClassification
from campaign_optimizer.llm.orchestrator import LocalLLMOrchestrator
from campaign_optimizer.llm.output_guard import OutputGuard
from campaign_optimizer.llm.prompt_builder import PromptBuilder
from campaign_optimizer.llm.request_builder import RequestBuilder
from campaign_optimizer.llm.retriever import LocalRuleRetriever
from campaign_optimizer.llm.session_store import SessionContext

ROOT = Path(__file__).parent / "fixtures" / "llm_eval" / "v1"
PLAN_ROOT = Path(__file__).parent / "fixtures" / "plan_a"


def _validator_module():
    spec = importlib.util.spec_from_file_location("llm_eval_v1_validator", ROOT / "validator.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load v1 validator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VALIDATOR = _validator_module()


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


class _FixtureClassifier:
    def __init__(self, fixture: dict[str, object]) -> None:
        self.fixture = fixture

    def classify(self, question, allowed_intents):
        fixture_type = self.fixture["fixture_type"]
        if fixture_type == "error":
            raise RuntimeError("synthetic classifier failure")
        if fixture_type == "invalid":
            return SimpleNamespace(
                intent=self.fixture["intent"], confidence=self.fixture["confidence"]
            )
        return RouterClassification(
            str(self.fixture["intent"]), float(self.fixture["confidence"])
        )


def _policy(case: dict[str, object]) -> HybridIntentPolicy:
    fixture = case.get("classifier_fixture")
    return HybridIntentPolicy(
        _FixtureClassifier(fixture) if isinstance(fixture, dict) else None
    )


def _routing_cases():
    for name in ("routing-safety.json", "prompt-attacks.json"):
        yield from _load(ROOT / name)["cases"]


def test_v1_validator_accepts_frozen_pilot():
    assert VALIDATOR.validate_all(ROOT) == 90


def test_all_70_router_and_attack_labels_match_policy():
    cases = list(_routing_cases())
    assert len(cases) == 70
    for case in cases:
        decision = _policy(case).resolve_chat(case["question"])
        expected = case["route_expectation"]
        assert decision.intent == case["expected_intent"], case["case_id"]
        assert decision.source == expected["source"], case["case_id"]
        assert decision.confidence == expected["confidence"], case["case_id"]
        assert (case["provider_access"] == "ALLOW") == (
            case["expected_provider_calls"] > 0
        )


def test_refusal_canaries_are_controlled_request_suffixes_and_never_reach_provider_or_envelope():
    plan = _load(PLAN_ROOT / "final_plan.demo.json")
    review = _load(PLAN_ROOT / "ontology_review.demo.json")
    for case in _routing_cases():
        if case["expected_status"] != "REFUSED":
            continue
        factory_calls = 0

        def forbidden_factory():
            nonlocal factory_calls
            factory_calls += 1
            raise AssertionError("controlled refusal must not construct a provider")

        service = LocalLLMOrchestrator(
            RequestBuilder(LocalRuleRetriever()),
            PromptBuilder(),
            client_factory=forbidden_factory,
            intent_policy=_policy(case),
        )
        result = service.run(
            plan,
            review,
            question=f"{case['question']} {case['canary']}",
            session_context=SessionContext("eval-tenant", "eval-user", case["case_id"]),
        )
        serialized = json.dumps(result.as_envelope(), ensure_ascii=False)
        assert result["status"] == "REFUSED", case["case_id"]
        assert result.refusal_reason == case["expected_reason"], case["case_id"]
        assert factory_calls == 0, case["case_id"]
        assert case["canary"] not in serialized, case["case_id"]


@pytest.mark.parametrize(
    "mutation",
    ["count", "duplicate_id", "broken_ref", "pair", "split", "canary"],
)
def test_validator_rejects_key_mutations(tmp_path, mutation):
    destination = tmp_path / "tests" / "fixtures" / "llm_eval" / "v1"
    shutil.copytree(ROOT, destination)
    shutil.copytree(PLAN_ROOT, destination.parents[1] / "plan_a")

    if mutation == "count":
        path = destination / "index.json"
        payload = _load(path)
        payload["datasets"][0]["case_count"] += 1
    elif mutation == "duplicate_id":
        path = destination / "prompt-attacks.json"
        payload = _load(path)
        payload["cases"][0]["case_id"] = "rs_001"
    elif mutation == "broken_ref":
        path = destination / "generation.json"
        payload = _load(path)
        payload["cases"][0]["frozen_input_id"] = "input_99"
    elif mutation == "pair":
        path = destination / "generation.json"
        payload = _load(path)
        payload["cases"][1]["candidate_id"] = "candidate_clean_review"
    elif mutation == "split":
        path = destination / "prompt-attacks.json"
        payload = _load(path)
        payload["cases"][0]["split"] = "dev"
    else:
        path = destination / "prompt-attacks.json"
        payload = _load(path)
        payload["cases"][0]["question"] += " " + payload["cases"][0]["canary"]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(VALIDATOR.DatasetValidationError):
        VALIDATOR.validate_all(destination)


def test_generation_pair_configs_and_candidate_guard_fixtures():
    generation = _load(ROOT / "generation.json")
    common = generation["common_executor_configs"]["executor_01"]
    assert {
        "runs", "seed", "model_alias", "max_output_tokens", "timeout_seconds"
    }.issubset(common)
    assert generation["arm_configs"]["baseline_01"]["max_revision_rounds"] == 0
    assert generation["arm_configs"]["reviewer_01"]["max_revision_rounds"] == 1
    assert all(case["observed_cost_cny"] is None for case in generation["cases"])
    assert all(case["observed_latency_ms"] is None for case in generation["cases"])

    plan = _load(PLAN_ROOT / "final_plan.demo.json")
    review = _load(PLAN_ROOT / "ontology_review.demo.json")
    guard = OutputGuard()
    fixtures = generation["candidate_fixtures"]

    # Every paired case executes the real deterministic guard against its own
    # frozen question/intent and referenced candidate fixture.
    for case in generation["cases"]:
        frozen = generation["frozen_inputs"][case["frozen_input_id"]]
        artifacts = RequestBuilder(LocalRuleRetriever()).build(
            plan,
            review,
            mode=frozen["mode"],
            question=frozen["question"],
            resolved_intent=frozen["expected_intent"],
        )
        candidate_meta = fixtures[case["candidate_id"]]
        candidate = _load(ROOT / candidate_meta["file"])
        validated = guard.validate(
            json.dumps(candidate),
            request=artifacts.request,
            plan=artifacts.context["plan_context"],
            review=artifacts.context["review_context"],
            context=artifacts.context,
            retry_count=0,
        )
        assert candidate_meta["expected_guard"] == "PASS", case["case_id"]
        assert validated["status"] == "OK", case["case_id"]
        assert validated["intent"] == frozen["expected_intent"], case["case_id"]
        expectation = case["arm_expectation"]
        assert expectation["provider_calls"] == case["expected_provider_calls"]
        assert expectation["status"] == case["expected_status"]

    # Numeric corruption remains a deterministic hard-gate control and is not
    # used to claim Reviewer quality.
    numeric_meta = fixtures["candidate_numeric_error_review"]
    numeric = _load(ROOT / numeric_meta["file"])
    artifacts = RequestBuilder(LocalRuleRetriever()).build(
        plan,
        review,
        mode="chat",
        question="Explain review.",
        resolved_intent="EXPLAIN_REVIEW",
    )
    assert numeric_meta["candidate_role"] == "hard_gate_only"
    assert numeric_meta["expected_reviewer_action"] == "NOT_APPLICABLE"
    with pytest.raises(ContractValidationError):
        guard.validate(
            json.dumps(numeric),
            request=artifacts.request,
            plan=artifacts.context["plan_context"],
            review=artifacts.context["review_context"],
            context=artifacts.context,
            retry_count=0,
        )