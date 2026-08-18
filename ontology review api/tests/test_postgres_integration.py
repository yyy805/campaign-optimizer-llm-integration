from __future__ import annotations

import json
import os
import warnings
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.config import Settings
from app.main import create_app
from tests.conftest import API_KEY, AUTH, ONTOLOGY_ROOT, PROJECT_ROOT


POSTGRES_URL = os.getenv("TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(
    not POSTGRES_URL,
    reason="TEST_POSTGRES_URL is not configured for a disposable PostgreSQL/PolarDB database",
)


def test_postgres_migration_persistence_and_idempotent_replay():
    assert POSTGRES_URL is not None
    parsed_url = make_url(POSTGRES_URL)
    assert parsed_url.get_backend_name() == "postgresql"
    assert parsed_url.get_driver_name() == "psycopg"
    assert parsed_url.database and parsed_url.database.endswith("_test"), (
        "TEST_POSTGRES_URL must target a dedicated database whose name ends with _test"
    )
    assert os.getenv("ALLOW_POSTGRES_TEST_MIGRATION") == "1", (
        "set ALLOW_POSTGRES_TEST_MIGRATION=1 to acknowledge test migrations and cleanup"
    )
    run_id = uuid4().hex
    idempotency_key = f"polar-integration-{run_id}"
    principal_id = f"polar-test-{run_id}"
    fixture_path = (
        PROJECT_ROOT
        / "tests"
        / "fixtures"
        / "plan_a"
        / "final_plan.demo.json"
    )
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    payload["plan_id"] = f"plan_polar_integration_{run_id}"
    settings = Settings(
        database_url=POSTGRES_URL,
        ontology_path=ONTOLOGY_ROOT,
        api_key_principals=f"{API_KEY}:{principal_id}:polar-integration:SERVICE",
        docs_enabled=False,
    )
    review_id: str | None = None
    try:
        with TestClient(create_app(settings)) as client:
            assert client.app.state.database is not None, (
                f"database startup failed: {client.app.state.startup_errors}"
            )
            assert client.app.state.database.engine.dialect.name == "postgresql"
            assert client.get("/ready").status_code == 200
            first = client.post(
                "/api/v1/plan-reviews",
                headers={**AUTH, "Idempotency-Key": idempotency_key},
                json=payload,
            )
            assert first.status_code == 201, first.text
            review_id = first.json()["review_id"]
            replay = client.post(
                "/api/v1/plan-reviews",
                headers={**AUTH, "Idempotency-Key": idempotency_key},
                json=payload,
            )
            assert replay.status_code == 201
            assert replay.json() == first.json()
            changed = json.loads(json.dumps(payload))
            changed["source_version"] = "conflicting-replay"
            conflict = client.post(
                "/api/v1/plan-reviews",
                headers={**AUTH, "Idempotency-Key": idempotency_key},
                json=changed,
            )
            assert conflict.status_code == 409
            assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
            with client.app.state.database.engine.connect() as connection:
                assert connection.execute(
                    text("SELECT count(*) FROM plan_reviews WHERE id = :review_id"),
                    {"review_id": review_id},
                ).scalar_one() == 1
                assert connection.execute(
                    text("SELECT count(*) FROM idempotency_records WHERE principal_id = :principal AND endpoint = :endpoint AND idempotency_key = :key"),
                    {"principal": principal_id, "endpoint": "/api/v1/plan-reviews", "key": idempotency_key},
                ).scalar_one() == 1

        with TestClient(create_app(settings)) as restarted:
            assert restarted.get("/ready").status_code == 200
            persisted = restarted.get(f"/api/v1/plan-reviews/{review_id}", headers=AUTH)
            assert persisted.status_code == 200
            assert persisted.json()["plan_id"] == payload["plan_id"]
            restarted_replay = restarted.post(
                "/api/v1/plan-reviews",
                headers={**AUTH, "Idempotency-Key": idempotency_key},
                json=payload,
            )
            assert restarted_replay.status_code == first.status_code
            assert restarted_replay.json() == first.json()
            assert restarted_replay.headers["X-Ontology-Checksum"] == first.headers["X-Ontology-Checksum"]
    finally:
        from app.db import Database

        database = None
        try:
            database = Database(POSTGRES_URL)
            with database.engine.begin() as connection:
                stored_review_id = connection.execute(
                    text("SELECT review_id FROM idempotency_records WHERE principal_id = :principal AND endpoint = :endpoint AND idempotency_key = :key"),
                    {"principal": principal_id, "endpoint": "/api/v1/plan-reviews", "key": idempotency_key},
                ).scalar_one_or_none()
                cleanup_review_id = review_id or stored_review_id
                connection.execute(
                    text("DELETE FROM idempotency_records WHERE principal_id = :principal AND endpoint = :endpoint AND idempotency_key = :key"),
                    {"principal": principal_id, "endpoint": "/api/v1/plan-reviews", "key": idempotency_key},
                )
                if cleanup_review_id is not None:
                    connection.execute(
                        text("DELETE FROM plan_reviews WHERE id = :review_id AND tenant = :tenant AND plan_id = :plan_id"),
                        {"review_id": cleanup_review_id, "tenant": "polar-integration", "plan_id": payload["plan_id"]},
                    )
        except Exception as exc:
            warnings.warn(f"PostgreSQL integration cleanup failed: {type(exc).__name__}", stacklevel=1)
        finally:
            if database is not None:
                database.close()
