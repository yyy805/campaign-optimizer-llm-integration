from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import jsonschema
import pytest
from datetime import datetime, timezone
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from campaign_optimizer.contracts.feedback import apply_feedback_event
from campaign_optimizer.contracts.validation import (
    ContractValidationError,
    validate_contract_object,
)
from campaign_optimizer.ontology.db import (
    ClientRow, FeedbackEventRow, OntologyReviewItemRow, OntologyReviewRow,
    PlanDecisionEventRow, PlanItemRow, PlanSnapshotRow, RuleConfidenceStateRow,
    apply_feedback_transaction, canonical_digest, init_db,
)

ROOT = Path(__file__).parent.parent


def _policy() -> dict:
    return json.loads(
        (ROOT / "campaign_optimizer/ontology/policies/feedback_policy.demo.json")
        .read_text(encoding="utf-8")
    )


def _state() -> dict:
    return {
        "schema_version": "1.0", "rule_id": "R1", "rule_version": "1.0",
        "base_confidence": 0.65, "runtime_confidence": 0.65,
        "minimum_usable_confidence": 0.5,
        "validation_count": 0, "rejection_count": 0,
        "consecutive_bad_count": 0, "status": "ACTIVE",
        "processed_feedback_ids": [], "processed_feedback_digests": {},
        "updated_at": "2026-08-03T00:00:00Z",
    }


def _event(rating: str = "GOOD", verdict: str = "SUPPORT", event_id: str = "fb-1") -> dict:
    return {
        "schema_version": "1.0", "feedback_id": event_id,
        "review_id": "review_1", "review_item_id": "review_item_1",
        "plan_id": "plan_1", "plan_item_id": "plan_item_1",
        "rule_id": "R1", "rule_version": "1.0", "verdict": verdict,
        "rating": rating, "actor_id": "user-1", "created_at": "2026-08-03T01:00:00Z",
    }


def _review(event: dict) -> dict:
    insufficient = event["verdict"] == "INSUFFICIENT_EVIDENCE"
    return {
        "schema_version": "1.0",
        "review_id": event["review_id"],
        "plan_id": event["plan_id"],
        "source": "DEMO_ONTOLOGY_STUB",
        "ontology_version": "test",
        "confidence_state_version": "test",
        "is_synthetic": True,
        "overall_verdict": event["verdict"],
        "items": [{
            "review_item_id": event["review_item_id"],
            "plan_item_id": event["plan_item_id"],
            "verdict": event["verdict"],
            "rule_id": event["rule_id"],
            "rule_version": event["rule_version"],
            "base_confidence": 0.65,
            "runtime_confidence": 0.65,
            "matched_fact_ids": [] if insufficient else ["review_fact_1"],
            "missing_evidence": ["required_signal"] if insufficient else [],
            "missing_rule_parameters": [],
            "limitations": [],
        }],
    }


def _apply(state: dict, event: dict) -> dict:
    return apply_feedback_event(state, event, _review(event), _policy())


def _seed_runtime(engine, event: dict | None = None, client_id: str = 'c1') -> None:
    feedback = event or _event()
    review = _review(feedback)
    now = datetime.now(timezone.utc)
    with Session(engine) as session:
        session.add(ClientRow(client_id=client_id, card={}))
        session.flush()
        session.add(PlanSnapshotRow(client_id=client_id, plan_id='plan_1', source_artifact_id=None,
            source_version='1', plan_digest=canonical_digest({}), created_at=now, payload={}))
        session.flush()
        session.add(PlanItemRow(client_id=client_id, plan_id='plan_1', plan_item_id='plan_item_1',
            entity_id='channel_1', action='increase_budget', payload={}))
        session.flush()
        session.add(OntologyReviewRow(client_id=client_id, review_id='review_1', plan_id='plan_1',
            ontology_version='test', overall_verdict=feedback['verdict'], created_at=now, payload=review))
        session.flush()
        session.add(OntologyReviewItemRow(client_id=client_id, review_id='review_1', review_item_id='review_item_1',
            plan_id='plan_1', plan_item_id='plan_item_1', rule_id='R1', rule_version='1.0',
            verdict=feedback['verdict'], confidence_snapshot=0.65, payload=review['items'][0]))
        session.add(RuleConfidenceStateRow(client_id=client_id, rule_id='R1', rule_version='1.0',
            runtime_confidence=0.65, status='ACTIVE', revision=0, updated_at=now, payload=_state()))
        session.commit()


def test_good_fine_bad_use_demo_policy_without_mutating_input():
    original = _state()
    good = _apply(original, _event("GOOD"))
    assert original["runtime_confidence"] == 0.65
    assert good["runtime_confidence"] == pytest.approx(0.67)
    fine = _apply(good, _event("FINE", event_id="fb-2"))
    assert fine["runtime_confidence"] == pytest.approx(0.67)
    bad = _apply(fine, _event("BAD", event_id="fb-3"))
    assert bad["runtime_confidence"] == pytest.approx(0.62)


def test_duplicate_event_is_idempotent():
    once = _apply(_state(), _event())
    twice = _apply(once, _event())
    assert twice == once


def test_insufficient_evidence_feedback_does_not_change_confidence():
    updated = _apply(_state(), _event("BAD", "INSUFFICIENT_EVIDENCE"))
    assert updated["runtime_confidence"] == 0.65
    assert updated["rejection_count"] == 0


def test_three_consecutive_bad_marks_rule_for_human_review():
    state = _state()
    for number in range(3):
        state = _apply(state, _event("BAD", event_id=f"fb-{number}"))
    assert state["status"] == "PENDING_HUMAN_REVIEW"


def test_feedback_cannot_update_another_rule_snapshot():
    event = _event()
    event["rule_version"] = "2.0"
    with pytest.raises(ContractValidationError):
        _apply(_state(), event)


def test_duplicate_id_with_changed_payload_is_rejected():
    once = _apply(_state(), _event())
    changed = _event("BAD")
    with pytest.raises(ContractValidationError, match="different payload"):
        _apply(once, changed)


def test_feedback_must_match_review_snapshot():
    event = _event()
    review = _review(event)
    review["items"][0]["verdict"] = "CONFLICT"
    review["overall_verdict"] = "CONFLICT"
    with pytest.raises(ContractValidationError, match="reviewed item snapshot"):
        apply_feedback_event(_state(), event, review, _policy())


def test_suspended_rule_rejects_feedback():
    state = _state()
    state["status"] = "SUSPENDED"
    with pytest.raises(ContractValidationError, match="cannot accept feedback"):
        _apply(state, _event())


def test_feedback_transaction_persists_and_replays_once(tmp_path):
    db_url = 'sqlite:///' + str(tmp_path / 'runtime.db')
    engine = init_db(db_url)
    feedback = _event()
    review = _review(feedback)
    now = datetime.now(timezone.utc)
    with Session(engine) as session:
        session.add(ClientRow(client_id='c1', card={}))
        session.flush()
        session.add(PlanSnapshotRow(client_id='c1', plan_id='plan_1', source_artifact_id=None,
            source_version='1', plan_digest=canonical_digest({}), created_at=now, payload={}))
        session.flush()
        session.add(PlanItemRow(client_id='c1', plan_id='plan_1', plan_item_id='plan_item_1',
            entity_id='channel_1', action='increase_budget', payload={}))
        session.flush()
        session.add(OntologyReviewRow(client_id='c1', review_id='review_1', plan_id='plan_1',
            ontology_version='test', overall_verdict='SUPPORT', created_at=now, payload=review))
        session.flush()
        session.add(OntologyReviewItemRow(client_id='c1', review_id='review_1', review_item_id='review_item_1',
            plan_id='plan_1', plan_item_id='plan_item_1', rule_id='R1', rule_version='1.0', verdict='SUPPORT',
            confidence_snapshot=0.65, payload=review['items'][0]))
        session.add(RuleConfidenceStateRow(client_id='c1', rule_id='R1', rule_version='1.0',
            runtime_confidence=0.65, status='ACTIVE', revision=0, updated_at=now, payload=_state()))
        session.commit()
    once = apply_feedback_transaction(engine, client_id='c1', event_payload=feedback, policy=_policy())
    twice = apply_feedback_transaction(engine, client_id='c1', event_payload=feedback, policy=_policy())
    assert once['application_status'] == 'APPLIED'
    assert twice['application_status'] == 'ALREADY_APPLIED'
    assert once['state']['runtime_confidence'] == pytest.approx(0.67)
    with Session(init_db(db_url)) as session:
        state = session.get(RuleConfidenceStateRow, ('c1', 'R1', '1.0'))
        assert state.revision == 1
        assert session.get(FeedbackEventRow, ('c1', 'fb-1')) is not None


def test_concurrent_feedback_updates_are_not_lost(tmp_path):
    engine = init_db('sqlite:///' + str(tmp_path / 'concurrent.db'))
    _seed_runtime(engine)
    events = [_event(event_id='fb-a'), _event(event_id='fb-b')]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda item: apply_feedback_transaction(
            engine, client_id='c1', event_payload=item, policy=_policy()), events))
    assert {result['application_status'] for result in results} == {'APPLIED'}
    with Session(engine) as session:
        state = session.get(RuleConfidenceStateRow, ('c1', 'R1', '1.0'))
        assert state.revision == 2
        assert state.runtime_confidence == pytest.approx(0.69)


def test_persisted_duplicate_id_with_changed_payload_is_rejected(tmp_path):
    engine = init_db('sqlite:///' + str(tmp_path / 'tampered.db'))
    _seed_runtime(engine)
    apply_feedback_transaction(engine, client_id='c1', event_payload=_event(), policy=_policy())
    changed = _event(rating='BAD')
    with pytest.raises(ContractValidationError, match='different payload'):
        apply_feedback_transaction(engine, client_id='c1', event_payload=changed, policy=_policy())
    with Session(engine) as session:
        state = session.get(RuleConfidenceStateRow, ('c1', 'R1', '1.0'))
        assert state.revision == 1
        assert state.runtime_confidence == pytest.approx(0.67)


def test_failed_feedback_rolls_back_event_and_state(tmp_path):
    engine = init_db('sqlite:///' + str(tmp_path / 'rollback.db'))
    _seed_runtime(engine)
    invalid = _event()
    invalid['plan_item_id'] = 'plan_item_wrong'
    with pytest.raises(ContractValidationError):
        apply_feedback_transaction(engine, client_id='c1', event_payload=invalid, policy=_policy())
    with Session(engine) as session:
        state = session.get(RuleConfidenceStateRow, ('c1', 'R1', '1.0'))
        assert state.revision == 0
        assert session.get(FeedbackEventRow, ('c1', 'fb-1')) is None


def test_persisted_feedback_is_immutable_and_uses_server_time(tmp_path):
    engine = init_db('sqlite:///' + str(tmp_path / 'immutable.db'))
    _seed_runtime(engine)
    result = apply_feedback_transaction(engine, client_id='c1', event_payload=_event(), policy=_policy())
    assert result['applied_revision'] == 1
    with Session(engine) as session:
        stored = session.get(FeedbackEventRow, ('c1', 'fb-1'))
        assert stored.received_at != stored.event_created_at
        assert stored.received_at.tzinfo == timezone.utc
        assert stored.event_created_at.tzinfo == timezone.utc
        stored.rating = 'BAD'
        with pytest.raises(ContractValidationError, match='immutable'):
            session.commit()


def test_illegal_plan_decision_is_rejected_by_database(tmp_path):
    engine = init_db('sqlite:///' + str(tmp_path / 'constraint.db'))
    _seed_runtime(engine)
    with Session(engine) as session:
        session.add(PlanDecisionEventRow(client_id='c1', decision_id='d1', plan_id='plan_1',
            decision='GOOD', actor_id='u1', created_at=datetime.now(timezone.utc), payload={}))
        with pytest.raises(IntegrityError):
            session.commit()


def test_review_item_must_reference_a_real_plan_item(tmp_path):
    engine = init_db('sqlite:///' + str(tmp_path / 'review-fk.db'))
    _seed_runtime(engine)
    payload = dict(_review(_event())['items'][0])
    payload['review_item_id'] = 'review_item_missing'
    payload['plan_item_id'] = 'plan_item_missing'
    with Session(engine) as session:
        session.add(OntologyReviewItemRow(client_id='c1', review_id='review_1',
            review_item_id='review_item_missing', plan_id='plan_1',
            plan_item_id='plan_item_missing', rule_id='R1', rule_version='1.0',
            verdict='SUPPORT', confidence_snapshot=0.65, payload=payload))
        with pytest.raises(IntegrityError):
            session.commit()


def test_plan_decision_is_a_separate_accept_reject_contract():
    event = {
        "schema_version": "1.0", "decision_id": "decision-1", "plan_id": "plan-1",
        "plan_source_version": "1.0", "plan_hash": "a" * 64,
        "decision": "ACCEPT", "actor_id": "user-1", "created_at": "2026-08-03T01:00:00Z",
    }
    validate_contract_object("plan_decision_event", event)
    event["decision"] = "GOOD"
    with pytest.raises(jsonschema.ValidationError):
        validate_contract_object("plan_decision_event", event)
