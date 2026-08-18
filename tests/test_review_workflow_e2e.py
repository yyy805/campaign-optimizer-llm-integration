from __future__ import annotations

import copy
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from campaign_optimizer.contracts.validation import ContractValidationError
from campaign_optimizer.ontology.db import (
    ClientRow,
    ModelArtifactRow,
    FeedbackEventRow,
    OntologyReviewRow,
    PlanSnapshotRow,
    RuleConfidenceStateRow,
    init_db,
)
from campaign_optimizer.ontology.publication import (
    PackageDriftError,
    build_publication_manifest,
    canonical_asset_bytes,
    verify_publication_manifest,
)
from campaign_optimizer.ontology.review_workflow import ReviewRelease, ReviewWorkflow

ROOT = Path(__file__).parent.parent
PLAN = ROOT / "tests" / "fixtures" / "plan_a" / "final_plan.demo.json"


def _plan() -> dict:
    return json.loads(PLAN.read_text(encoding="utf-8"))


def _workflow(tmp_path: Path) -> tuple[ReviewWorkflow, object]:
    engine = init_db(f"sqlite:///{tmp_path / 'review.db'}")
    with Session(engine) as session, session.begin():
        session.add(ClientRow(client_id="client-1", card={"client_id": "client-1"}))
    manifest = build_publication_manifest(
        source_commit="a" * 40,
        ontology_version="2.0-campaign-pending",
        rule_version="R5@2.0-campaign-pending",
        engine_version="2.0",
        schema_version="1.1",
        root=ROOT,
    )
    release = ReviewRelease.from_manifest(
        manifest, confidence_state_version="unprovisioned", root=ROOT,
    )
    return ReviewWorkflow(engine, release), engine


def _count(engine: object, row_type: type) -> int:
    with Session(engine) as session:
        return session.scalar(select(func.count()).select_from(row_type)) or 0


def _r1_workflow(tmp_path: Path) -> tuple[ReviewWorkflow, object]:
    engine = init_db(f"sqlite:///{tmp_path / 'r1-review.db'}")
    manifest = build_publication_manifest(
        source_commit="a" * 40, ontology_version="test-r1",
        rule_version="R1@1.2-contract-hardening", engine_version="2.0",
        schema_version="1.1", root=ROOT,
    )
    release = ReviewRelease.from_manifest(
        manifest, confidence_state_version="database", root=ROOT,
    )
    state = {
        "schema_version": "1.0", "rule_id": "R1",
        "rule_version": "1.2-contract-hardening", "base_confidence": 0.65,
        "runtime_confidence": 0.65, "minimum_usable_confidence": 0.5,
        "validation_count": 0, "rejection_count": 0,
        "consecutive_bad_count": 0, "status": "ACTIVE",
        "processed_feedback_ids": [], "processed_feedback_digests": {},
        "updated_at": "2026-08-03T00:00:00Z",
    }
    with Session(engine) as session, session.begin():
        session.add(ClientRow(client_id="client-1", card={"client_id": "client-1"}))
        session.add(RuleConfidenceStateRow(
            client_id="client-1", rule_id="R1", rule_version="1.2-contract-hardening",
            runtime_confidence=0.65, status="ACTIVE", revision=0,
            updated_at=datetime.now(timezone.utc), payload=state,
        ))
    return ReviewWorkflow(engine, release), engine


def _r1_plan() -> dict:
    plan = _plan()
    plan["review_evidence"] = [
        {"fact_id": "review_fact_acos", "plan_item_id": "plan_item_001",
         "entity_type": "campaign", "entity_id": "Sponsored Products",
         "name": "acos", "value": 0.5, "baseline_value": 0.3,
         "baseline_source": "account", "baseline_period": "current_14_days",
         "unit": "ratio", "period": "current_14_days", "source": "test",
         "scope": "ontology_review"},
        {"fact_id": "review_fact_ctr", "plan_item_id": "plan_item_001",
         "entity_type": "campaign", "entity_id": "Sponsored Products",
         "name": "ctr", "value": 0.01, "baseline_value": 0.02,
         "baseline_source": "account", "baseline_period": "current_14_days",
         "unit": "ratio", "period": "current_14_days", "source": "test",
         "scope": "ontology_review"},
    ]
    return plan


def _stub_review_engine(
    plan, *, ontology_version, confidence_state_version, release_identity,
    confidence_states, rules_dir, enabled_rule_ids,
):
    state = confidence_states["R1"]
    item = {
        "review_item_id": "review_item_r1", "plan_item_id": "plan_item_001",
        "verdict": "SUPPORT", "rule_id": "R1",
        "rule_version": "1.2-contract-hardening", "base_confidence": 0.65,
        "runtime_confidence": state["runtime_confidence"],
        "matched_fact_ids": ["review_fact_acos"], "missing_evidence": [],
        "missing_rule_parameters": [], "limitations": [],
    }
    return {
        "schema_version": "1.0",
        "review_id": "review_" + confidence_state_version,
        "plan_id": plan["plan_id"], "source": "ONTOLOGY_ENGINE",
        "ontology_version": ontology_version, "release_identity": release_identity,
        "confidence_state_version": confidence_state_version,
        "is_synthetic": True, "overall_verdict": "SUPPORT", "items": [item],
    }


def test_initial_seed_is_archive_only_and_replays(tmp_path):
    workflow, engine = _workflow(tmp_path)
    seed = {
        "schema_version": "4.0",
        "campaign_group_id": "CG_DEMO_001",
        "candidate_pool_id": "pool_demo",
        "recommendation_type": "INITIAL_SEED",
        "handoff_status": "READY_FOR_OPTIMIZATION",
        "is_optimized": False,
        "campaigns": [{"campaign_id": "C_DEMO", "campaign_mta_score": 0.5}],
    }
    first = workflow.review_final_plan(client_id="client-1", plan=seed)
    second = workflow.review_final_plan(client_id="client-1", plan=seed)
    assert first == second
    assert first["status"] == "INITIAL_SEED_NOT_REVIEWABLE"
    assert _count(engine, ModelArtifactRow) == 1
    assert _count(engine, PlanSnapshotRow) == 0
    assert _count(engine, OntologyReviewRow) == 0


def test_review_commits_once_and_never_uses_pending_r5(tmp_path):
    workflow, engine = _workflow(tmp_path)
    first = workflow.review_final_plan(client_id="client-1", plan=_plan())
    second = workflow.review_final_plan(client_id="client-1", plan=_plan())
    assert first == second
    assert first["review"]["overall_verdict"] == "UNVERIFIED"
    assert first["review"]["items"][0]["rule_id"] is None
    assert _count(engine, PlanSnapshotRow) == 1
    assert _count(engine, OntologyReviewRow) == 1


@pytest.mark.parametrize('mutation', ['missing_optimized', 'channel_grain'])
def test_non_final_or_non_campaign_plan_is_archive_only(tmp_path, mutation):
    workflow, engine = _workflow(tmp_path)
    payload = _plan()
    if mutation == 'missing_optimized':
        payload.pop('is_optimized')
    else:
        payload['items'][0]['entity_type'] = 'channel'
        for fact in payload['decision_evidence'] + payload['review_evidence']:
            fact['entity_type'] = 'channel'
    result = workflow.review_final_plan(client_id='client-1', plan=payload)
    assert result['status'] in {'INITIAL_SEED_NOT_REVIEWABLE', 'ARCHIVED'}
    assert _count(engine, ModelArtifactRow) == 1
    assert _count(engine, PlanSnapshotRow) == 0
    assert _count(engine, OntologyReviewRow) == 0


def test_same_plan_id_with_changed_payload_conflicts(tmp_path):
    workflow, _engine = _workflow(tmp_path)
    workflow.review_final_plan(client_id="client-1", plan=_plan())
    changed = copy.deepcopy(_plan())
    changed["source_version"] = "1.1"
    with pytest.raises(ContractValidationError, match="immutable payload"):
        workflow.review_final_plan(client_id="client-1", plan=changed)


def test_manifest_is_deterministic_and_detects_drift(tmp_path):
    asset = tmp_path / "campaign_optimizer" / "ontology" / "asset.json"
    asset.parent.mkdir(parents=True)
    asset.write_text('{"a":1}\n', encoding="utf-8")
    manifest = build_publication_manifest(
        source_commit="a" * 40,
        ontology_version="2.0-campaign-pending",
        rule_version="R5@2.0-campaign-pending",
        engine_version="2.0",
        schema_version="1.0",
        root=tmp_path,
    )
    verify_publication_manifest(manifest, root=tmp_path)
    assert manifest["entries"][0]["size"] == len(canonical_asset_bytes(asset))
    asset.write_text('{"a":2}\n', encoding="utf-8")
    with pytest.raises(PackageDriftError):
        verify_publication_manifest(manifest, root=tmp_path)


def test_feedback_then_linked_rereview_uses_latest_confidence_revision(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "campaign_optimizer.ontology.review_workflow.generate_ontology_review",
        _stub_review_engine,
    )
    workflow, engine = _r1_workflow(tmp_path)
    first = workflow.review_final_plan(
        client_id="client-1", plan=_r1_plan(), enabled_rule_ids=("R1",)
    )
    item = first["review"]["items"][0]
    event = {
        "schema_version": "1.0", "feedback_id": "fb-rereview-1",
        "review_id": first["review"]["review_id"],
        "review_item_id": item["review_item_id"], "plan_id": first["review"]["plan_id"],
        "plan_item_id": item["plan_item_id"], "rule_id": item["rule_id"],
        "rule_version": item["rule_version"], "verdict": item["verdict"],
        "rating": "GOOD", "actor_id": "tester",
        "created_at": "2026-08-05T00:00:00Z",
    }
    policy = json.loads((
        ROOT / "campaign_optimizer/ontology/policies/feedback_policy.demo.json"
    ).read_text(encoding="utf-8"))
    applied = workflow.apply_feedback(
        client_id="client-1", event_payload=event, policy=policy
    )
    second = workflow.rereview(
        client_id="client-1", prior_review_id=first["review"]["review_id"],
        enabled_rule_ids=("R1",),
    )
    assert applied["applied_revision"] == 1
    assert second["parent_review_id"] == first["review"]["review_id"]
    assert second["revision"] == 1
    assert second["review"]["review_id"] != first["review"]["review_id"]
    assert second["review"]["items"][0]["runtime_confidence"] == pytest.approx(0.67)
    assert second["review"]["confidence_state_version"] != first["review"]["confidence_state_version"]
    with Session(engine) as session:
        stored = session.get(OntologyReviewRow, ("client-1", second["review"]["review_id"]))
        assert stored.parent_review_id == first["review"]["review_id"]
        assert stored.revision == 1
        assert stored.package_checksum == second["review"]["release_identity"]["package_checksum"]
        assert _count(engine, FeedbackEventRow) == 1


def test_concurrent_same_rereview_replays_one_revision(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "campaign_optimizer.ontology.review_workflow.generate_ontology_review",
        _stub_review_engine,
    )
    workflow, engine = _r1_workflow(tmp_path)
    first = workflow.review_final_plan(
        client_id="client-1", plan=_r1_plan(), enabled_rule_ids=("R1",)
    )
    prior_id = first["review"]["review_id"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(
            lambda _index: workflow.rereview(
                client_id="client-1", prior_review_id=prior_id,
                enabled_rule_ids=("R1",),
            ),
            range(2),
        ))
    assert results[0]["review"] == results[1]["review"]
    assert _count(engine, OntologyReviewRow) == 2


def test_missing_provisioned_state_rolls_back_rereview(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "campaign_optimizer.ontology.review_workflow.generate_ontology_review",
        _stub_review_engine,
    )
    workflow, engine = _r1_workflow(tmp_path)
    first = workflow.review_final_plan(
        client_id="client-1", plan=_r1_plan(), enabled_rule_ids=("R1",)
    )
    with Session(engine) as session, session.begin():
        state = session.get(
            RuleConfidenceStateRow,
            ("client-1", "R1", "1.2-contract-hardening"),
        )
        session.delete(state)
    with pytest.raises(ContractValidationError, match="not provisioned"):
        workflow.rereview(
            client_id="client-1", prior_review_id=first["review"]["review_id"],
            enabled_rule_ids=("R1",),
        )
    assert _count(engine, OntologyReviewRow) == 1


def test_rereview_branch_conflict_rolls_back_without_partial_revision(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "campaign_optimizer.ontology.review_workflow.generate_ontology_review",
        _stub_review_engine,
    )
    workflow, engine = _r1_workflow(tmp_path)
    first = workflow.review_final_plan(
        client_id="client-1", plan=_r1_plan(), enabled_rule_ids=("R1",)
    )
    workflow.rereview(
        client_id="client-1", prior_review_id=first["review"]["review_id"],
        enabled_rule_ids=("R1",),
    )
    with Session(engine) as session, session.begin():
        state = session.get(
            RuleConfidenceStateRow,
            ("client-1", "R1", "1.2-contract-hardening"),
        )
        state.revision = 1
        state.confidence = 0.71
        state.updated_at = datetime.now(timezone.utc)
    with pytest.raises(ContractValidationError, match="revision conflict"):
        workflow.rereview(
            client_id="client-1", prior_review_id=first["review"]["review_id"],
            enabled_rule_ids=("R1",),
        )
    assert _count(engine, OntologyReviewRow) == 2


def test_previous_bundle_recovery_is_explicit_and_current_drift_fails_closed(tmp_path):
    previous_root = tmp_path / 'previous'
    current_root = tmp_path / 'current'
    for root, value in ((previous_root, 1), (current_root, 2)):
        asset = root / 'campaign_optimizer' / 'ontology' / 'asset.json'
        asset.parent.mkdir(parents=True)
        asset.write_text(json.dumps({'asset': value}) + '\n', encoding='utf-8')
    previous = build_publication_manifest(
        source_commit='a' * 40, ontology_version='previous',
        rule_version='R5@previous', engine_version='1.0',
        schema_version='1.0', root=previous_root,
    )
    current = build_publication_manifest(
        source_commit='b' * 40, ontology_version='current',
        rule_version='R5@current', engine_version='2.0',
        schema_version='1.1', root=current_root,
    )
    (current_root / 'campaign_optimizer' / 'ontology' / 'asset.json').write_text(
        '{"asset":"drift"}\n', encoding='utf-8'
    )

    with pytest.raises(PackageDriftError):
        ReviewRelease.from_manifest(
            current, confidence_state_version='current', root=current_root,
        )
    recovered = ReviewRelease.from_manifest(
        previous, confidence_state_version='previous', root=previous_root,
    )
    assert recovered.source_commit == 'a' * 40
    assert recovered.package_checksum == previous['package_checksum']
