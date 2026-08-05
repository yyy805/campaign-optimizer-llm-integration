"""Transactional product-review workflow around the canonical engine."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from contextlib import contextmanager

from sqlalchemy.engine import Engine
from sqlalchemy import select
from sqlalchemy.orm import Session

from campaign_optimizer.contracts.validation import (
    ContractValidationError,
    validate_contract_object,
)

from .db import (
    ClientRow,
    ModelArtifactRow,
    OntologyReviewItemRow,
    OntologyReviewRow,
    PlanItemRow,
    PlanSnapshotRow,
    RuleConfidenceStateRow,
    apply_feedback_transaction,
    canonical_digest,
)
from .review_engine import generate_ontology_review
from .publication import PROJECT_ROOT, verify_publication_manifest


def is_initial_seed(payload: dict[str, Any]) -> bool:
    """Recognize the approved seed shape without interpreting its metrics."""
    return (
        payload.get("recommendation_type") == "INITIAL_SEED"
        or payload.get("is_optimized") is not True
        or payload.get("handoff_status") == "READY_FOR_OPTIMIZATION"
    )


def _artifact_id(payload: dict[str, Any]) -> str:
    identity = payload.get("candidate_pool_id") or payload.get("campaign_group_id")
    return f"artifact_{identity or canonical_digest(payload)[:20]}"


@dataclass(frozen=True, init=False)
class ReviewRelease:
    ontology_version: str
    rule_version: str
    engine_version: str
    schema_version: str
    confidence_state_version: str
    source_commit: str
    package_checksum: str

    @classmethod
    def from_manifest(
        cls, manifest: dict[str, Any], *, confidence_state_version: str,
        root: Path = PROJECT_ROOT,
    ) -> ReviewRelease:
        verify_publication_manifest(manifest, root=root)
        release = object.__new__(cls)
        for name in (
            'ontology_version', 'rule_version', 'engine_version',
            'schema_version', 'source_commit', 'package_checksum',
        ):
            object.__setattr__(release, name, str(manifest[name]))
        object.__setattr__(
            release, 'confidence_state_version', confidence_state_version
        )
        return release

    def identity(self) -> dict[str, str]:
        return {
            'ontology_version': self.ontology_version,
            'rule_version': self.rule_version,
            'engine_version': self.engine_version,
            'schema_version': self.schema_version,
            'source_commit': self.source_commit,
            'package_checksum': self.package_checksum,
        }


class ReviewWorkflow:
    """Own the SQLite publication boundary; LLM work starts after return."""

    def __init__(self, engine: Engine, release: ReviewRelease) -> None:
        self.engine = engine
        self.release = release

    def archive_model_artifact(
        self, *, client_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        digest = canonical_digest(payload)
        artifact_id = _artifact_id(payload)
        with Session(self.engine) as session, session.begin():
            self._require_client(session, client_id)
            existing = session.get(ModelArtifactRow, (client_id, artifact_id))
            if existing is not None and existing.content_digest != digest:
                raise ContractValidationError(
                    "model artifact identity has a different payload"
                )
            if existing is None:
                session.add(ModelArtifactRow(
                    client_id=client_id,
                    artifact_id=artifact_id,
                    artifact_type=str(payload.get("recommendation_type", "UPSTREAM_PAYLOAD")),
                    schema_version=payload.get("schema_version"),
                    source_model="SMALL_MODEL_CHAIN",
                    source_model_version=payload.get("source_version"),
                    period_start=None,
                    period_end=None,
                    parent_artifact_id=None,
                    content_digest=digest,
                    created_at=datetime.now(timezone.utc),
                    payload=payload,
                ))
        return {
            "status": "INITIAL_SEED_NOT_REVIEWABLE" if is_initial_seed(payload) else "ARCHIVED",
            "artifact_id": artifact_id,
            "content_digest": digest,
        }

    def review_final_plan(
        self,
        *,
        client_id: str,
        plan: dict[str, Any],
        confidence_states: dict[str, dict[str, Any]] | None = None,
        enabled_rule_ids: tuple[str, ...] = (),
        parent_review_id: str | None = None,
    ) -> dict[str, Any]:
        if is_initial_seed(plan):
            return self.archive_model_artifact(client_id=client_id, payload=plan)
        try:
            validate_contract_object('final_plan', plan)
        except Exception:
            # Non-final and legacy-grain upstream payloads remain auditable model
            # artifacts, but must never enter the product review tables.
            return self.archive_model_artifact(client_id=client_id, payload=plan)
        if confidence_states is not None:
            raise ContractValidationError(
                'product workflow loads confidence only from provisioned database state'
            )
        with self._write_session() as session:
            return self._review_in_session(
                session, client_id=client_id, plan=plan,
                enabled_rule_ids=enabled_rule_ids,
                parent_review_id=parent_review_id,
            )

    @contextmanager
    def _write_session(self):
        connection = self.engine.connect()
        if self.engine.dialect.name == 'sqlite':
            connection.exec_driver_sql('BEGIN IMMEDIATE')
            session = Session(bind=connection, join_transaction_mode='control_fully')
        else:
            session = Session(bind=connection)
            session.begin()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
            connection.close()

    def _load_confidence_states(
        self, session: Session, *, client_id: str,
        enabled_rule_ids: tuple[str, ...],
    ) -> tuple[dict[str, dict[str, Any]], str]:
        states: dict[str, dict[str, Any]] = {}
        revisions: list[tuple[str, str, int]] = []
        expected_versions = {
            item.split('@', 1)[0]: item.split('@', 1)[1]
            for item in self.release.rule_version.split(',') if '@' in item
        }
        for rule_id in enabled_rule_ids:
            rule_version = expected_versions.get(rule_id)
            if rule_version is None:
                raise ContractValidationError(
                    f'release does not pin an enabled version for {rule_id}'
                )
            row = session.get(
                RuleConfidenceStateRow, (client_id, rule_id, rule_version)
            )
            if row is None:
                raise ContractValidationError(
                    f'confidence state is not provisioned for {rule_id}@{rule_version}'
                )
            states[rule_id] = dict(row.payload)
            revisions.append((rule_id, rule_version, row.revision))
        version = (
            'no-enabled-rules' if not revisions
            else 'confidence_' + canonical_digest({'revisions': revisions})[:20]
        )
        return states, version

    def _review_in_session(
        self, session: Session, *, client_id: str, plan: dict[str, Any],
        enabled_rule_ids: tuple[str, ...], parent_review_id: str | None,
    ) -> dict[str, Any]:
        self._require_client(session, client_id)
        parent = None
        revision = 0
        if parent_review_id is not None:
            parent = session.get(OntologyReviewRow, (client_id, parent_review_id))
            if parent is None or parent.plan_id != plan['plan_id']:
                raise ContractValidationError('parent review does not match the plan')
            revision = parent.revision + 1
        states, confidence_version = self._load_confidence_states(
            session, client_id=client_id, enabled_rule_ids=enabled_rule_ids
        )
        review = generate_ontology_review(
            plan, ontology_version=self.release.ontology_version,
            confidence_state_version=confidence_version,
            confidence_states=states,
            release_identity=self.release.identity(),
            enabled_rule_ids=enabled_rule_ids,
        )
        if parent_review_id is not None:
            review['review_id'] = 'review_' + canonical_digest({
                'base_review_id': review['review_id'],
                'parent_review_id': parent_review_id,
                'revision': revision,
            })[:16]
        self._persist_review_session(
            session, client_id=client_id, plan=plan, review=review,
            parent_review_id=parent_review_id, revision=revision,
        )
        return {
            'status': 'COMMITTED', 'review': review,
            'parent_review_id': parent_review_id, 'revision': revision,
            'release': {**self.release.identity(),
                        'confidence_state_version': confidence_version},
        }

    def rereview(
        self, *, client_id: str, prior_review_id: str,
        enabled_rule_ids: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        with self._write_session() as session:
            prior = session.get(OntologyReviewRow, (client_id, prior_review_id))
            if prior is None:
                raise ContractValidationError('prior review does not exist')
            snapshot = session.get(PlanSnapshotRow, (client_id, prior.plan_id))
            if snapshot is None:
                raise ContractValidationError('prior review plan snapshot does not exist')
            return self._review_in_session(
                session, client_id=client_id, plan=dict(snapshot.payload),
                enabled_rule_ids=enabled_rule_ids,
                parent_review_id=prior_review_id,
            )

    def apply_feedback(
        self, *, client_id: str, event_payload: dict[str, Any],
        policy: dict[str, Any],
    ) -> dict[str, Any]:
        return apply_feedback_transaction(
            self.engine, client_id=client_id,
            event_payload=event_payload, policy=policy,
        )

    def _persist_review_session(
        self, session: Session, *, client_id: str, plan: dict[str, Any],
        review: dict[str, Any], parent_review_id: str | None, revision: int,
    ) -> None:
        digest = canonical_digest(plan)
        now = datetime.now(timezone.utc)
        snapshot = session.get(PlanSnapshotRow, (client_id, plan['plan_id']))
        if snapshot is not None and snapshot.plan_digest != digest:
            raise ContractValidationError('plan_id already identifies a different immutable payload')
        if snapshot is None:
            session.add(PlanSnapshotRow(
                client_id=client_id, plan_id=plan['plan_id'], source_artifact_id=None,
                source_version=plan['source_version'], plan_digest=digest,
                created_at=now, payload=plan,
            ))
            session.flush()
            for item in plan['items']:
                session.add(PlanItemRow(
                    client_id=client_id, plan_id=plan['plan_id'],
                    plan_item_id=item['plan_item_id'], entity_id=item['entity_id'],
                    action=item['action'], payload=item,
                ))
            session.flush()
        existing = session.get(OntologyReviewRow, (client_id, review['review_id']))
        if existing is not None:
            if existing.payload != review:
                raise ContractValidationError('review_id already identifies a different result')
            return
        existing_revision = session.scalar(select(OntologyReviewRow).where(
            OntologyReviewRow.client_id == client_id,
            OntologyReviewRow.plan_id == plan['plan_id'],
            OntologyReviewRow.revision == revision,
        ))
        if existing_revision is not None:
            raise ContractValidationError(
                'review revision conflict: this plan revision already exists'
            )
        identity = review['release_identity']
        session.add(OntologyReviewRow(
            client_id=client_id, review_id=review['review_id'], plan_id=plan['plan_id'],
            parent_review_id=parent_review_id, revision=revision,
            ontology_version=identity['ontology_version'],
            rule_version=identity['rule_version'], engine_version=identity['engine_version'],
            schema_version=identity['schema_version'], source_commit=identity['source_commit'],
            package_checksum=identity['package_checksum'],
            confidence_state_version=review['confidence_state_version'],
            overall_verdict=review['overall_verdict'], created_at=now, payload=review,
        ))
        session.flush()
        for item in review['items']:
            session.add(OntologyReviewItemRow(
                client_id=client_id, review_id=review['review_id'],
                review_item_id=item['review_item_id'], plan_id=plan['plan_id'],
                plan_item_id=item['plan_item_id'], rule_id=item['rule_id'],
                rule_version=item['rule_version'], verdict=item['verdict'],
                confidence_snapshot=item['runtime_confidence'], payload=item,
            ))

    def _persist_review(
        self, *, client_id: str, plan: dict[str, Any], review: dict[str, Any]
    ) -> None:
        digest = canonical_digest(plan)
        now = datetime.now(timezone.utc)
        with Session(self.engine) as session, session.begin():
            self._require_client(session, client_id)
            snapshot = session.get(PlanSnapshotRow, (client_id, plan["plan_id"]))
            if snapshot is not None and snapshot.plan_digest != digest:
                raise ContractValidationError(
                    "plan_id already identifies a different immutable payload"
                )
            if snapshot is None:
                session.add(PlanSnapshotRow(
                    client_id=client_id, plan_id=plan["plan_id"],
                    source_artifact_id=None, source_version=plan["source_version"],
                    plan_digest=digest, created_at=now, payload=plan,
                ))
                session.flush()
                for item in plan["items"]:
                    session.add(PlanItemRow(
                        client_id=client_id, plan_id=plan["plan_id"],
                        plan_item_id=item["plan_item_id"], entity_id=item["entity_id"],
                        action=item["action"], payload=item,
                    ))
                session.flush()
            existing = session.get(OntologyReviewRow, (client_id, review["review_id"]))
            if existing is not None:
                if existing.payload != review:
                    raise ContractValidationError(
                        "review_id already identifies a different result"
                    )
                return
            session.add(OntologyReviewRow(
                client_id=client_id, review_id=review["review_id"],
                plan_id=plan["plan_id"], ontology_version=review["ontology_version"],
                overall_verdict=review["overall_verdict"], created_at=now,
                payload=review,
            ))
            session.flush()
            for item in review["items"]:
                session.add(OntologyReviewItemRow(
                    client_id=client_id, review_id=review["review_id"],
                    review_item_id=item["review_item_id"], plan_id=plan["plan_id"],
                    plan_item_id=item["plan_item_id"], rule_id=item["rule_id"],
                    rule_version=item["rule_version"], verdict=item["verdict"],
                    confidence_snapshot=item["runtime_confidence"], payload=item,
                ))

    @staticmethod
    def _require_client(session: Session, client_id: str) -> None:
        if session.get(ClientRow, client_id) is None:
            raise ContractValidationError("client is not provisioned")
