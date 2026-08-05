"""Offline gates for the frozen reviewer judgment v1 dataset; zero provider construction."""
from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import pytest

from campaign_optimizer.contracts.validation import ContractValidationError
from campaign_optimizer.llm.agent_workflow_v5 import ReviewerPacket
from campaign_optimizer.llm.agent_workflow_v12 import load_role_configuration, max_provider_calls_v12
from campaign_optimizer.llm.output_guard import OutputGuard
from campaign_optimizer.llm.request_builder import LLMVersions, RequestBuilder
from campaign_optimizer.llm.retriever import LocalRuleRetriever, RetrievalError, RetrievalErrorCode
from campaign_optimizer.llm.reviewer_binding_v13 import validate_reviewer_binding_v13
from campaign_optimizer.llm.three_role_runner import _candidate_id
from campaign_optimizer.llm.three_role_runner_v13 import ThreeRoleRunnerV13

ROOT = Path(__file__).parent / "fixtures" / "llm_eval" / "reviewer_judgment_v1"
PLAN_ROOT = Path(__file__).parent / "fixtures" / "plan_a"


def _validator_module():
    spec = importlib.util.spec_from_file_location("reviewer_judgment_v1_validator", ROOT / "validator.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load reviewer judgment v1 validator")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VALIDATOR = _validator_module()


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _cases():
    return _load(ROOT / "cases.json")["cases"]


def pending_chain():
    plan = _load(PLAN_ROOT / "final_plan.demo.json")
    review = _load(ROOT / "ontology_review.pending.json")
    versions = LLMVersions(
        workflow_version="not-published",
        prompt_version="draft-1.0",
        knowledge_base_version="not-published",
    )
    artifacts = RequestBuilder(LocalRuleRetriever(), versions=versions).build(
        plan, review, mode="initial_render", question="请解释本次推荐方案和本体评价。", resolved_intent="EXPLAIN_REVIEW",
    )
    return plan, review, artifacts


def candidate_for(case):
    return _load(ROOT / case["candidate_file"])


def expected_decision_shape(case, packet):
    value = {
        "schema_version": "1.0",
        "candidate_id": packet.candidate_id,
        "packet_digest": packet.packet_digest,
        "decision": case["expected_decision"],
        "violation_codes": [],
        "evidence_source_ids": [],
        "revision_actions": [],
    }
    if case["expected_decision"] == "REVISE":
        value.update(
            violation_codes=case["acceptable_violation_codes"][:1],
            evidence_source_ids=["review_item_pending"],
            revision_actions=[{"operation": "ADD_REQUIRED_LIMITATION", "target_claim_id": None, "source_id": "review_item_pending"}],
        )
    elif case["expected_decision"] == "REJECT":
        value.update(violation_codes=["SAFETY_VIOLATION"], evidence_source_ids=["review_item_pending"])
    return value


def test_validator_accepts_frozen_dataset():
    assert VALIDATOR.validate_all(ROOT) == 8


@pytest.mark.parametrize(
    "mutation",
    ["count", "duplicate_id", "broken_candidate_ref", "rule_field_claim", "verdict_tamper", "label_mismatch"],
)
def test_validator_rejects_key_mutations(tmp_path, mutation):
    destination = tmp_path / "tests" / "fixtures" / "llm_eval" / "reviewer_judgment_v1"
    shutil.copytree(ROOT, destination)
    shutil.copytree(PLAN_ROOT, destination.parents[1] / "plan_a")

    if mutation == "count":
        path = destination / "index.json"
        payload = _load(path)
        payload["datasets"][0]["case_count"] += 1
    elif mutation == "duplicate_id":
        path = destination / "cases.json"
        payload = _load(path)
        payload["cases"][1]["case_id"] = payload["cases"][0]["case_id"]
    elif mutation == "broken_candidate_ref":
        path = destination / "cases.json"
        payload = _load(path)
        payload["cases"][0]["candidate_file"] = "candidates/pending_missing.json"
    elif mutation == "rule_field_claim":
        path = destination / "candidates" / "pending_pass_clean.json"
        payload = _load(path)
        payload["claims"].append({"claim_id": "claim_rule", "claim_type": "RULE_FIELD", "source_id": "R5", "field": "status", "value": "ACTIVE"})
        payload["rule_ids_used"] = ["R5"]
    elif mutation == "verdict_tamper":
        path = destination / "candidates" / "pending_revise_definitive_verdict.json"
        payload = _load(path)
        payload["claims"][4]["value"] = "CONFLICT"
    else:
        path = destination / "cases.json"
        payload = _load(path)
        payload["cases"][3]["label_class"] = "explain_only"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(VALIDATOR.DatasetValidationError):
        VALIDATOR.validate_all(destination)


def test_pending_review_binds_canonical_identity_and_yields_empty_rule_context():
    plan, review, artifacts = pending_chain()
    assert artifacts.context["review_context"]["release_identity"] == review["release_identity"]
    assert artifacts.context["review_context"]["overall_verdict"] == "UNVERIFIED"
    assert artifacts.context["public_rule_context"] == []
    assert artifacts.context["allowed_rule_ids"] == []
    with pytest.raises(RetrievalError) as caught:
        LocalRuleRetriever().retrieve(["R5"], "explain", {"R5": "2.0-campaign-pending"})
    assert caught.value.code is RetrievalErrorCode.INACTIVE_RULE


def test_every_candidate_passes_output_guard_and_reaches_reviewer():
    plan, review, artifacts = pending_chain()
    guard = OutputGuard()
    for case in _cases():
        validated = guard.validate(
            json.dumps(candidate_for(case)),
            request=artifacts.request,
            plan=artifacts.context["plan_context"],
            review=artifacts.context["review_context"],
            context=artifacts.context,
            retry_count=0,
        )
        assert validated["status"] == "OK", case["case_id"]
        assert validated["intent"] == "EXPLAIN_REVIEW", case["case_id"]


def test_every_expected_decision_is_contract_reachable():
    plan, review, artifacts = pending_chain()
    config = load_role_configuration()
    for index, case in enumerate(_cases()):
        packet = ReviewerPacket.from_validated_exchange(
            request=artifacts.request, plan=plan, review=review, context=artifacts.context,
            candidate_output=candidate_for(case), resolved_intent="EXPLAIN_REVIEW",
            candidate_id=_candidate_id(str(artifacts.request["request_id"]), index), retry_count=0, config=config.roles,
        )
        validate_reviewer_binding_v13(expected_decision_shape(case, packet), packet=packet)


def test_dry_run_chain_makes_zero_provider_calls():
    plan, review, artifacts = pending_chain()
    result = ThreeRoleRunnerV13().run(
        request=artifacts.request, plan=plan, review=review, context=artifacts.context,
        revision_profile="baseline", dry_run=True,
    )
    assert result.status == "DRY_RUN" and result.provider_calls == 0
    assert result.reserved_provider_calls == max_provider_calls_v12(0, False)
    assert all(call.outcome == "RESERVED" for call in result.calls)


def test_v14_eval_dry_run_reports_plan_without_provider():
    import subprocess
    import sys
    root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [sys.executable, str(root / "scripts" / "run_reviewer_judgment_eval_v14.py")],
        cwd=root, capture_output=True, text=True, check=True,
    )
    out = json.loads(completed.stdout)
    assert out["status"] == "DRY_RUN" and out["case_count"] == 8
    assert out["decision_distribution"] == {"PASS": 3, "REJECT": 1, "REVISE": 4}
    assert out["label_class_distribution"] == {"explain_only": 2, "refuse_assertion": 3, "pending_review_semantics": 2, "safety": 1}
    assert out["reviewer_call_limit"] == 16


def test_v14_eval_single_case_selection_dry():
    import subprocess
    import sys
    root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [sys.executable, str(root / "scripts" / "run_reviewer_judgment_eval_v14.py"), "--case", "pending_revise_denies_review"],
        cwd=root, capture_output=True, text=True, check=True,
    )
    out = json.loads(completed.stdout)
    assert out["status"] == "DRY_RUN" and out["case_count"] == 1
    assert out["selected_case"] == "pending_revise_denies_review" and out["reviewer_call_limit"] == 2


def test_rule_field_claim_is_rejected_before_reviewer():
    plan, review, artifacts = pending_chain()
    candidate = _load(ROOT / "candidates" / "pending_pass_clean.json")
    candidate["claims"].append({"claim_id": "claim_rule", "claim_type": "RULE_FIELD", "source_id": "R5", "field": "status", "value": "ACTIVE"})
    candidate["rule_ids_used"] = ["R5"]
    with pytest.raises(ContractValidationError):
        OutputGuard().validate(
            json.dumps(candidate),
            request=artifacts.request,
            plan=artifacts.context["plan_context"],
            review=artifacts.context["review_context"],
            context=artifacts.context,
            retry_count=0,
        )
