from __future__ import annotations

import logging
import re
import time
from contextlib import asynccontextmanager
from threading import Lock
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.router import router as v1_router
from app.contracts import ExternalContractSchemas
from app.config import Settings, get_settings
from app.db import Database
from app.errors import AppError
from app.logging import configure_logging
from app.ontology import load_ontology
from app.services.review_engine import ReviewEngine
from app.services.plan_review_service import PlanReviewService


logger = logging.getLogger("ontology_review_api")


def initialize_database(database_url: str) -> Database:
    database: Database | None = None
    try:
        database = Database(database_url)
        database.migrate()
        return database
    except Exception:
        if database is not None:
            database.close()
        raise


def error_body(request: Request, code: str, message: str, retryable: bool, details=None) -> dict:
    return {
        "error": {
            "code": code,
            "class": "business" if code not in {"INTERNAL_ERROR", "DATABASE_UNAVAILABLE"} else "system",
            "retryable": retryable,
            "message": message,
            "correlation_id": getattr(request.state, "request_id", None),
            "details": details,
        }
    }


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.startup_errors = []
        app.state.ontology = None
        app.state.engine = None
        app.state.database = None
        app.state.external_contracts = None
        app.state.product_review_service = None
        try:
            app.state.principals = settings.principals()
        except Exception as exc:
            app.state.startup_errors.append({"component": "auth", "message": str(exc)})
        try:
            ontology = load_ontology(settings.ontology_path)
            app.state.ontology = ontology
            app.state.engine = ReviewEngine(ontology)
        except Exception as exc:
            app.state.startup_errors.append({"component": "ontology", "message": str(exc)})
            logger.error("ontology startup validation failed", extra={"error_code": "ONTOLOGY_UNAVAILABLE"})
        try:
            app.state.external_contracts = ExternalContractSchemas(
                settings.final_plan_schema_path, settings.ontology_review_schema_path,
            )
        except Exception as exc:
            app.state.startup_errors.append({"component": "contracts", "message": str(exc)})
            logger.error("external contract startup validation failed", extra={"error_code": "CONTRACTS_UNAVAILABLE"})
        try:
            app.state.database = initialize_database(settings.database_url)
        except Exception as exc:
            app.state.startup_errors.append({"component": "database", "message": str(exc)})
            logger.error("database startup validation failed", extra={"error_code": "DATABASE_UNAVAILABLE"})
        try:
            app.state.product_review_service = PlanReviewService(
                settings.database_url, settings.plan_review_client_id
            )
        except Exception as exc:
            app.state.startup_errors.append({"component": "product_review", "message": str(exc)})
            logger.error("product review startup validation failed", extra={"error_code": "ONTOLOGY_UNAVAILABLE"})
        yield
        if app.state.database is not None:
            app.state.database.close()

    app = FastAPI(
        title="Ontology Review API",
        version="0.1.0",
        description="Deterministic Ontology review with immutable provenance.",
        lifespan=lifespan,
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.principals = {}
    app.state.ontology = None
    app.state.engine = None
    app.state.database = None
    app.state.external_contracts = None
    app.state.product_review_service = None
    app.state.startup_errors = []
    app.state.database_lock = Lock()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "X-API-Key", "Idempotency-Key", "X-Request-ID"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        candidate = request.headers.get("X-Request-ID", "")
        request.state.request_id = candidate if re.fullmatch(r"[A-Za-z0-9._:-]{1,100}", candidate) else str(uuid4())
        start = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        logger.info(
            "request completed",
            extra={"request_id": request.state.request_id, "duration_ms": round((time.perf_counter() - start) * 1000, 2)},
        )
        return response

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError):
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(request, exc.code, exc.message, exc.retryable, exc.details),
            headers={"X-Request-ID": request.state.request_id},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content=jsonable_encoder(error_body(request, "VALIDATION_ERROR", "request validation failed", False, exc.errors())),
            headers={"X-Request-ID": request.state.request_id},
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception):
        logger.exception("unhandled request error", extra={"request_id": request.state.request_id, "error_code": "INTERNAL_ERROR"})
        return JSONResponse(
            status_code=500,
            content=error_body(request, "INTERNAL_ERROR", "internal service error", True),
            headers={"X-Request-ID": request.state.request_id},
        )

    @app.get("/health", tags=["operations"])
    def health():
        return {"status": "alive"}

    @app.get("/ready", tags=["operations"])
    def ready(request: Request):
        ontology = request.app.state.ontology
        database = request.app.state.database
        if database is None:
            with request.app.state.database_lock:
                if request.app.state.database is None:
                    try:
                        request.app.state.database = initialize_database(settings.database_url)
                        request.app.state.startup_errors = [
                            item for item in request.app.state.startup_errors
                            if item["component"] != "database"
                        ]
                    except Exception:
                        logger.error(
                            "database readiness recovery failed",
                            extra={"error_code": "DATABASE_UNAVAILABLE"},
                        )
                database = request.app.state.database
        db_ready = database is not None and database.check()
        auth_ready = bool(request.app.state.principals)
        contracts_ready = request.app.state.external_contracts is not None
        product_service = request.app.state.product_review_service
        if ontology is None or product_service is None or not db_ready or not auth_ready or not contracts_ready:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "not_ready",
                    "ontology_ready": product_service is not None,
                    "database_ready": db_ready,
                    "auth_ready": auth_ready,
                    "contracts_ready": contracts_ready,
                    "migration": "unavailable" if database is None else "incomplete",
                    "errors": [{"component": item["component"], "code": f"{item['component'].upper()}_UNAVAILABLE"} for item in request.app.state.startup_errors],
                },
            )
        return {
            "status": "ready",
            "ontology_ready": True,
            "database_ready": True,
            "auth_ready": True,
            "contracts_ready": True,
            "migration": "applied",
            "ontology_version": product_service.ontology_version,
            "ontology_checksum": product_service.package_checksum,
            "rules": product_service.rule_statuses,
            "guardrails": product_service.guardrail_ids,
        }

    app.include_router(v1_router)
    return app


app = create_app()
