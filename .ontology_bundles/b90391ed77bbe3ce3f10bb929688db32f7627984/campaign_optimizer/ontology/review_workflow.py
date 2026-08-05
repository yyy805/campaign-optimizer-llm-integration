"""Transactional product-review workflow around the canonical engine."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.engine import Engine
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
    ) -> dict[str, Any]:
        if is_initial_seed(plan):
            return self.archive_model_artifact(client_id=client_id, payload=plan)
        try:
            validate_contract_object('final_plan', plan)
        except Exception:
            # Non-final and legacy-grain upstream payloads remain auditable model
            # artifacts, but must never enter the product review tables.
            return self.archive_model_artifact(client_id=client_id, payload=plan)
        review = generate_ontology_review(
            plan,
            ontology_version=self.release.ontology_version,
            confidence_state_version=self.release.confidence_state_version,
            confidence_states=confidence_states,
            release_identity=self.release.identity(),
        )
        self._persist_review(client_id=client_id, plan=plan, review=review)
        return {
            "status": "COMMITTED",
            "review": review,
            "release": {**self.release.identity(),
                        "confidence_state_version": self.release.confidence_state_version},
        }

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
