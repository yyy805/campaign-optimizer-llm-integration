"""Release gate for the canonical three-role local workflow."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from campaign_optimizer.llm.agent_workflow_v5 import ReviewerPacket, WorkflowAction, load_role_configuration, max_provider_calls_with_repairs, max_role_calls, next_action, validate_triage_decision


ROOT = Path(__file__).parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "plan_a"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _packet(*, retry_count: int = 0, candidate_retry_count: int | None = None) -> ReviewerPacket:
    candidate = _fixture("llm_workflow_output.demo.json")
    candidate["retry_count"] = retry_count if candidate_retry_count is None else candidate_retry_count
    return ReviewerPacket.from_validated_exchange(
        request=_fixture("llm_request.demo.json"), plan=_fixture("final_plan.demo.json"), review=_fixture("ontology_review.demo.json"), context=_fixture("llm_context.demo.json"),
        candidate_output=candidate, resolved_intent="EXPLAIN_REVIEW", candidate_id="candidate_case_01", retry_count=retry_count, config=load_role_configuration(),
    )


def _decision(packet: ReviewerPacket, decision: str, *, operation: str = "ADD_REQUIRED_LIMITATION", target: str | None = None) -> dict:
    result = {"schema_version": "1.0", "candidate_id": packet.candidate_id, "packet_digest": packet.packet_digest, "decision": decision, "violation_codes": [], "evidence_source_ids": [], "revision_actions": []}
    if decision == "REVISE":
        result.update({"violation_codes": ["MISSING_LIMITATION"], "evidence_source_ids": ["review_item_pending"], "revision_actions": [{"operation": operation, "target_claim_id": target, "source_id": "review_item_pending"}]})
    if decision == "REJECT":
        result["violation_codes"] = ["UNRESOLVABLE_CONFLICT"]
    return result


def test_pinned_prompts_have_explicit_model_role_and_schema_versions():
    config = load_role_configuration()
    assert config.model_aliases == {"triage": "qwen3.7-plus", "executor": "qwen3.7-max", "reviewer": "qwen3.8-max-preview"}
    assert config.prompt_versions == {"triage": "triage_v2", "executor": "executor_v2", "reviewer": "reviewer_v3"}
    assert config.revision_profiles == {"baseline": 0, "production_candidate": 1, "experiment": 3, "stress_only": 5}

def test_role_model_aliases_are_the_exact_bailian_api_model_ids():
    """The runner sends these values unchanged to the provider client."""
    config = load_role_configuration()
    assert config.model_aliases == {
        "triage": "qwen3.7-plus",
        "executor": "qwen3.7-max",
        "reviewer": "qwen3.8-max-preview",
    }


def test_changed_pinned_prompt_hash_fails_closed(tmp_path: Path):
    config = json.loads((ROOT / "campaign_optimizer" / "llm" / "agent_roles.v5.json").read_text(encoding="utf-8"))
    config["expected_prompt_hashes"]["reviewer"] = "0" * 64
    alternate = tmp_path / "roles.json"
    alternate.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(ValueError, match="hash"):
        load_role_configuration(alternate)


def test_triage_route_and_abstain_contracts_are_unambiguous():
    validate_triage_decision({"schema_version": "1.0", "agent_role": "TRIAGE", "prompt_version": "triage_v2", "decision": "ROUTE", "intent": "EXPLAIN_PLAN", "confidence": 0.8, "reason_code": "CLEAR_SINGLE_EXPLANATION"})
    validate_triage_decision({"schema_version": "1.0", "agent_role": "TRIAGE", "prompt_version": "triage_v2", "decision": "ABSTAIN", "intent": None, "confidence": 0.2, "reason_code": "AMBIGUOUS"})


def test_reviewer_packet_excludes_question_and_binds_candidate_attempt():
    assert "question" not in json.dumps(_packet().as_model_input(), ensure_ascii=False)
    with pytest.raises(ValueError):
        _packet(retry_count=1, candidate_retry_count=0)


def test_reviewer_decision_must_echo_the_server_packet_digest():
    packet = _packet()
    replay = _decision(packet, "PASS")
    replay["packet_digest"] = "0" * 64
    with pytest.raises(ValueError, match="server-issued reviewer packet"):
        next_action(replay, packet=packet, revision_rounds=0, max_revision_rounds=1)


def test_typed_revision_actions_cannot_escape_the_current_candidate_or_context():
    packet = _packet()
    assert next_action(_decision(packet, "REVISE"), packet=packet, revision_rounds=0, max_revision_rounds=1) is WorkflowAction.REVISE
    with pytest.raises(ValueError):
        next_action(_decision(packet, "REVISE", operation="REMOVE_UNSUPPORTED_CLAIM"), packet=packet, revision_rounds=0, max_revision_rounds=1)
    with pytest.raises(ValueError):
        next_action(_decision(packet, "REVISE", operation="CORRECT_CLAIM_TO_SOURCE", target="claim_missing"), packet=packet, revision_rounds=0, max_revision_rounds=1)
    with pytest.raises(ValueError):
        next_action(_decision(packet, "REVISE", operation="ADD_REQUIRED_LIMITATION", target="claim_001"), packet=packet, revision_rounds=0, max_revision_rounds=1)


@pytest.mark.parametrize("limit,roles,providers", [(0, 2, 3), (1, 4, 6), (3, 8, 12), (5, 12, 18)])
def test_final_reviewer_and_repair_budget_reservations(limit: int, roles: int, providers: int):
    packet = _packet()
    assert max_role_calls(max_revision_rounds=limit) == roles
    assert max_provider_calls_with_repairs(max_revision_rounds=limit) == providers
    assert next_action(_decision(packet, "REVISE"), packet=packet, revision_rounds=limit, max_revision_rounds=limit) is WorkflowAction.FALLBACK
    assert next_action(_decision(packet, "PASS"), packet=packet, revision_rounds=limit, max_revision_rounds=limit) is WorkflowAction.FINAL
