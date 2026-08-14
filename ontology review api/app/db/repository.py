from __future__ import annotations

import hashlib
import json
from datetime import timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import IdempotencyRow, ReviewRow
from pydantic import BaseModel

from app.domain.models import ReviewCreate, ReviewResponse, ReviewStatus
from app.errors import AppError
from app.services.review_engine import EngineResult


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def request_hash(request: BaseModel) -> str:
    return hashlib.sha256(canonical_json(request.model_dump(mode="json")).encode()).hexdigest()


def value_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def row_to_response(row: ReviewRow) -> ReviewResponse:
    created_at = row.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return ReviewResponse.model_validate({
        "review_id": row.id,
        "schema_version": row.schema_version,
        "tenant": row.tenant,
        "client_id": row.client_id,
        "entity": json.loads(row.entity_json),
        "original_request": json.loads(row.original_request_json),
        "outcome": row.outcome,
        "disposition": row.disposition,
        "reason": row.reason,
        "matched_rules": json.loads(row.matched_rules_json),
        "winner_rule": row.winner_rule,
        "suppressed_rules": json.loads(row.suppressed_rules_json),
        "action": json.loads(row.action_json) if row.action_json else None,
        "rule_evaluations": json.loads(row.rule_evaluations_json),
        "guardrail_evaluations": json.loads(row.guardrail_evaluations_json),
        "evidence_refs": json.loads(row.evidence_refs_json),
        "evidence_status": row.evidence_status,
        "ontology_version": row.ontology_version,
        "ontology_checksum": row.ontology_checksum,
        "status": row.status,
        "principal_id": row.principal_id,
        "request_id": row.request_id,
        "record_version": row.record_version,
        "created_at": created_at,
    })


class ReviewRepository:
    def __init__(self, session: Session):
        self.session = session

    def existing_idempotency(self, principal_id: str, endpoint: str, key: str, digest: str) -> dict[str, Any] | None:
        row = self.session.scalar(select(IdempotencyRow).where(
            IdempotencyRow.principal_id == principal_id,
            IdempotencyRow.endpoint == endpoint,
            IdempotencyRow.idempotency_key == key,
        ))
        if row is None:
            return None
        if row.request_hash != digest:
            raise AppError(409, "IDEMPOTENCY_CONFLICT", "idempotency key was already used with a different payload")
        return json.loads(row.response_json)

    def create(
        self,
        request: ReviewCreate,
        result: EngineResult,
        *,
        tenant: str,
        principal_id: str,
        request_id: str,
        ontology_version: str,
        ontology_checksum: str,
        idempotency_key: str,
    ) -> ReviewResponse:
        digest = request_hash(request)
        existing = self.existing_idempotency(principal_id, "/api/v1/reviews", idempotency_key, digest)
        if existing:
            return ReviewResponse.model_validate(existing)
        row = ReviewRow(
            id=str(uuid4()),
            schema_version="review-v1",
            tenant=tenant,
            client_id=request.client_id,
            entity_json=canonical_json(request.entity.model_dump(mode="json")),
            original_request_json=canonical_json(request.model_dump(mode="json")),
            outcome=result.outcome.value,
            disposition=result.disposition.value,
            reason=result.reason,
            matched_rules_json=canonical_json(result.matched_rules),
            winner_rule=result.winner_rule,
            suppressed_rules_json=canonical_json(result.suppressed_rules),
            action_json=canonical_json(result.action.model_dump(mode="json")) if result.action else None,
            rule_evaluations_json=canonical_json([item.model_dump(mode="json") for item in result.rule_evaluations]),
            guardrail_evaluations_json=canonical_json([item.model_dump(mode="json") for item in result.guardrail_evaluations]),
            evidence_refs_json=canonical_json([item.model_dump(mode="json") for item in request.evidence_refs]),
            evidence_status="AVAILABLE" if request.evidence_refs else "PENDING",
            ontology_version=ontology_version,
            ontology_checksum=ontology_checksum,
            status=ReviewStatus.PENDING_USER_REVIEW.value,
            principal_id=principal_id,
            request_id=request_id,
            record_version=1,
        )
        self.session.add(row)
        self.session.flush()
        response = row_to_response(row)
        self.session.add(IdempotencyRow(
            principal_id=principal_id,
            endpoint="/api/v1/reviews",
            idempotency_key=idempotency_key,
            request_hash=digest,
            status_code=201,
            response_json=canonical_json(response.model_dump(mode="json")),
            review_id=row.id,
        ))
        try:
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            existing = self.existing_idempotency(principal_id, "/api/v1/reviews", idempotency_key, digest)
            if existing:
                return ReviewResponse.model_validate(existing)
            raise AppError(409, "IDEMPOTENCY_CONFLICT", "idempotency request collided") from exc
        return response

    def get(self, review_id: str, tenant: str) -> ReviewResponse:
        row = self.session.scalar(select(ReviewRow).where(ReviewRow.id == review_id, ReviewRow.tenant == tenant))
        if row is None:
            raise AppError(404, "REVIEW_NOT_FOUND", "review was not found")
        return row_to_response(row)

    def list(
        self,
        tenant: str,
        *,
        page: int,
        page_size: int,
        client_id: str | None,
        outcome: str | None,
        status: str | None,
        rule_id: str | None,
        ontology_version: str | None,
    ) -> tuple[list[ReviewResponse], int]:
        conditions = [ReviewRow.tenant == tenant]
        if client_id:
            conditions.append(ReviewRow.client_id == client_id)
        if outcome:
            conditions.append(ReviewRow.outcome == outcome)
        if status:
            conditions.append(ReviewRow.status == status)
        if ontology_version:
            conditions.append(ReviewRow.ontology_version == ontology_version)
        if rule_id:
            conditions.append(ReviewRow.matched_rules_json.like(f'%"{rule_id}"%'))
        total = self.session.scalar(select(func.count()).select_from(ReviewRow).where(*conditions)) or 0
        rows = self.session.scalars(
            select(ReviewRow).where(*conditions).order_by(ReviewRow.created_at.desc(), ReviewRow.id.desc()).offset((page - 1) * page_size).limit(page_size)
        ).all()
        return [row_to_response(row) for row in rows], total
