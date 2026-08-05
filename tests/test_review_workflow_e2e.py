from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from campaign_optimizer.contracts.validation import ContractValidationError
from campaign_optimizer.ontology.db import (
    ClientRow,
    ModelArtifactRow,
    OntologyReviewRow,
    PlanSnapshotRow,
    init_db,
)
from campaign_optimizer.ontology.publication import (
    PackageDriftError,
    build_publication_manifest,
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
    assert manifest["entries"][0]["size"] == asset.stat().st_size
    asset.write_text('{"a":2}\n', encoding="utf-8")
    with pytest.raises(PackageDriftError):
        verify_publication_manifest(manifest, root=tmp_path)
