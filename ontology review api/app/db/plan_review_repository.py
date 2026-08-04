from __future__ import annotations

import json
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import IdempotencyRow, PlanReviewRow
from app.db.repository import ReviewRepository, canonical_json
from app.domain.plan_review_models import FinalPlan, OntologyReview
from app.errors import AppError


ENDPOINT = "/api/v1/plan-reviews"


class PlanReviewRepository:
    def __init__(self, session: Session):
        self.session = session

    def existing(self, principal_id: str, key: str, digest: str) -> OntologyReview | None:
        value = ReviewRepository(self.session).existing_idempotency(principal_id, ENDPOINT, key, digest)
        return OntologyReview.model_validate(value) if value is not None else None

    def create(self, raw_plan: dict, plan: FinalPlan, review: OntologyReview, *, digest: str, tenant: str,
               client_id: str, principal_id: str, request_id: str,
               ontology_checksum: str, idempotency_key: str) -> OntologyReview:
        existing = self.existing(principal_id, idempotency_key, digest)
        if existing is not None:
            return existing
        self.session.add(PlanReviewRow(
            id=review.review_id, plan_id=review.plan_id, tenant=tenant, client_id=client_id,
            original_request_json=canonical_json(raw_plan),
            normalized_request_json=canonical_json(plan.model_dump(mode="json")),
            response_json=canonical_json(review.model_dump(mode="json")),
            ontology_version=review.ontology_version, ontology_checksum=ontology_checksum,
            principal_id=principal_id, request_id=request_id,
        ))
        self.session.add(IdempotencyRow(
            principal_id=principal_id, endpoint=ENDPOINT, idempotency_key=idempotency_key,
            request_hash=digest, status_code=201,
            response_json=canonical_json(review.model_dump(mode="json")), review_id=review.review_id,
        ))
        try:
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            existing = self.existing(principal_id, idempotency_key, digest)
            if existing is not None:
                return existing
            raise AppError(500, "DATABASE_WRITE_FAILED", "plan review could not be persisted", retryable=True) from exc
        return review

    def get(self, review_id: str, tenant: str) -> tuple[OntologyReview, str]:
        row = self.session.scalar(select(PlanReviewRow).where(
            PlanReviewRow.id == review_id, PlanReviewRow.tenant == tenant,
        ))
        if row is None:
            raise AppError(404, "PLAN_REVIEW_NOT_FOUND", "plan review was not found")
        try:
            return OntologyReview.model_validate(json.loads(row.response_json)), row.ontology_checksum
        except (json.JSONDecodeError, ValueError) as exc:
            raise AppError(500, "CORRUPT_PLAN_REVIEW", "stored plan review is invalid") from exc
