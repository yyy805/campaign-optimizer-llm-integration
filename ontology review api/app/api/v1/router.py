from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session

from app.db.repository import ReviewRepository
from app.db.repository import request_hash, value_hash
from app.db.plan_review_repository import PlanReviewRepository
from app.domain.plan_review_models import FinalPlan, OntologyReview
from pydantic import ValidationError
from app.domain.models import OntologyVersionResponse, ReviewCreate, ReviewList, ReviewResponse
from app.errors import AppError
from app.security import Principal, require_roles
from app.services.plan_review_service import PlanReviewService


router = APIRouter(prefix="/api/v1")


def get_session(request: Request):
    database = request.app.state.database
    if database is None or not database.check():
        raise AppError(503, "DATABASE_UNAVAILABLE", "database is not ready", retryable=True)
    with database.session_factory() as session:
        yield session


def get_runtime(request: Request):
    if request.app.state.ontology is None or request.app.state.engine is None:
        raise AppError(503, "ONTOLOGY_UNAVAILABLE", "ontology package is not ready", retryable=False)
    return request.app.state.ontology, request.app.state.engine


read_principal = require_roles("SERVICE", "REVIEWER", "GOVERNANCE_APPROVER", "PUBLISHER", "ADMIN")
create_principal = require_roles("SERVICE", "REVIEWER", "ADMIN")


@router.get("/ontology/version", response_model=OntologyVersionResponse)
def ontology_version(request: Request, _: Principal = Depends(read_principal)) -> OntologyVersionResponse:
    service = request.app.state.product_review_service
    if service is None:
        raise AppError(503, "ONTOLOGY_UNAVAILABLE", "canonical review workflow is unavailable")
    return OntologyVersionResponse(
        version=service.ontology_version,
        checksum=service.package_checksum,
        concepts=service.concept_ids,
        rules=service.rule_statuses,
        guardrails=service.guardrail_ids,
        clients=service.client_ids,
    )


@router.post("/reviews", response_model=ReviewResponse, status_code=201)
def create_review(
    payload: ReviewCreate,
    request: Request,
    response: Response,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", min_length=1, max_length=255)] = None,
    principal: Principal = Depends(create_principal),
    session: Session = Depends(get_session),
) -> ReviewResponse:
    if not idempotency_key:
        raise AppError(400, "IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key header is required")
    ontology, engine = get_runtime(request)
    repository = ReviewRepository(session)
    replay = repository.existing_idempotency(
        principal.principal_id,
        "/api/v1/reviews",
        idempotency_key,
        request_hash(payload),
    )
    if replay is not None:
        saved = ReviewResponse.model_validate(replay)
        response.headers["Location"] = f"/api/v1/reviews/{saved.review_id}"
        return saved
    result = engine.evaluate(payload)
    saved = repository.create(
        payload,
        result,
        tenant=principal.tenant,
        principal_id=principal.principal_id,
        request_id=request.state.request_id,
        ontology_version=ontology.version,
        ontology_checksum=ontology.checksum,
        idempotency_key=idempotency_key,
    )
    # A replay returns the immutable original body and request ID. Its HTTP status remains 201.
    response.headers["Location"] = f"/api/v1/reviews/{saved.review_id}"
    return saved


@router.post("/plan-reviews", response_model=OntologyReview, status_code=201)
def create_plan_review(
    payload: dict,
    request: Request,
    response: Response,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", min_length=1, max_length=255)] = None,
    principal: Principal = Depends(create_principal),
    session: Session = Depends(get_session),
) -> OntologyReview:
    if not idempotency_key:
        raise AppError(400, "IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key header is required")
    digest = value_hash(payload)
    repository = PlanReviewRepository(session)
    replay = repository.existing(principal.principal_id, idempotency_key, digest)
    if replay is not None:
        _stored, checksum = repository.get(replay.review_id, principal.tenant)
        response.headers["Location"] = f"/api/v1/plan-reviews/{replay.review_id}"
        response.headers["X-Ontology-Checksum"] = checksum
        return replay
    service = request.app.state.product_review_service
    if service is None:
        raise AppError(503, "ONTOLOGY_UNAVAILABLE", "canonical review workflow is unavailable")
    result = service.review(payload)
    if result["status"] != "COMMITTED":
        raise AppError(
            422, result["status"], "payload was archived and is not reviewable",
            details={"artifact_id": result["artifact_id"], "content_digest": result["content_digest"]},
        )
    try:
        plan = FinalPlan.model_validate(payload)
        review = OntologyReview.model_validate(result["review"])
    except ValidationError as exc:
        raise AppError(500, "CORE_CONTRACT_MISMATCH", "canonical workflow returned an invalid contract") from exc
    contracts = request.app.state.external_contracts
    if contracts is None:
        raise AppError(503, "CONTRACTS_UNAVAILABLE", "external contracts are unavailable")
    contracts.validate_ontology_review(review.model_dump(mode="json"))
    saved = repository.create(
        payload, plan, review, digest=digest, tenant=principal.tenant, client_id=service.client_id,
        principal_id=principal.principal_id, request_id=request.state.request_id,
        ontology_checksum=service.package_checksum, idempotency_key=idempotency_key,
    )
    response.headers["Location"] = f"/api/v1/plan-reviews/{saved.review_id}"
    response.headers["X-Ontology-Checksum"] = service.package_checksum
    return saved


@router.get("/plan-reviews/{review_id}", response_model=OntologyReview)
def get_plan_review(
    review_id: str,
    response: Response,
    principal: Principal = Depends(read_principal),
    session: Session = Depends(get_session),
) -> OntologyReview:
    review, checksum = PlanReviewRepository(session).get(review_id, principal.tenant)
    response.headers["X-Ontology-Checksum"] = checksum
    return review


@router.get("/reviews/{review_id}", response_model=ReviewResponse)
def get_review(
    review_id: str,
    principal: Principal = Depends(read_principal),
    session: Session = Depends(get_session),
) -> ReviewResponse:
    return ReviewRepository(session).get(review_id, principal.tenant)


@router.get("/reviews", response_model=ReviewList)
def list_reviews(
    principal: Principal = Depends(read_principal),
    session: Session = Depends(get_session),
    page: Annotated[int, Query(ge=1, le=10_000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    client_id: str | None = None,
    outcome: str | None = Query(default=None, pattern="^(MATCH|CONFLICT|NO_COVERAGE)$"),
    status: str | None = Query(default=None, pattern="^PENDING_USER_REVIEW$"),
    rule_id: str | None = Query(default=None, pattern="^R[1-7]$"),
    ontology_version: str | None = None,
) -> ReviewList:
    items, total = ReviewRepository(session).list(
        principal.tenant,
        page=page,
        page_size=page_size,
        client_id=client_id,
        outcome=outcome,
        status=status,
        rule_id=rule_id,
        ontology_version=ontology_version,
    )
    return ReviewList(items=items, page=page, page_size=page_size, total=total)
