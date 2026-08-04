from __future__ import annotations

import json
import subprocess

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import Settings
from app.domain.models import ReviewCreate
from app.main import create_app
from tests.conftest import API_KEY, AUTH, ONTOLOGY_ROOT, review_payload


R3_INPUT = [{"concept": "mta_roas", "value": 1.6, "baseline": 1.0, "source": "test"}]


def test_health_readiness_version_and_docs(client: TestClient):
    assert client.get("/health").json() == {"status": "alive"}
    ready = client.get("/ready")
    assert ready.status_code == 200
    assert ready.json()["ontology_version"] == "v1.1-demo"
    assert set(ready.json()["rules"]) == {f"R{number}" for number in range(1, 8)}
    assert client.get("/docs").status_code == 200
    assert client.get("/api/v1/ontology/version").status_code == 401
    version = client.get("/api/v1/ontology/version", headers=AUTH).json()
    assert version["rules"]["R7"] == "RETIRED"


def test_create_get_list_and_full_provenance(client: TestClient):
    headers = {**AUTH, "Idempotency-Key": "create-r3"}
    created = client.post("/api/v1/reviews", headers=headers, json=review_payload(["R3"], R3_INPUT))
    assert created.status_code == 201
    body = created.json()
    assert body["outcome"] == "MATCH"
    assert body["disposition"] == "REVIEW"
    assert body["ontology_version"] == "v1.1-demo"
    assert len(body["ontology_checksum"]) == 64
    assert body["principal_id"] == "test-agent"
    assert body["original_request"]["inputs"] == R3_INPUT
    assert body["rule_evaluations"][0]["conditions"][0]["passed"] is True
    review_id = body["review_id"]
    assert client.get(f"/api/v1/reviews/{review_id}", headers=AUTH).json() == body
    listed = client.get("/api/v1/reviews?outcome=MATCH&rule_id=R3", headers=AUTH).json()
    assert listed["total"] == 1
    assert listed["items"][0]["review_id"] == review_id


def test_idempotency_same_payload_and_conflict(client: TestClient):
    headers = {**AUTH, "Idempotency-Key": "retry-key"}
    payload = review_payload(["R3"], R3_INPUT)
    first = client.post("/api/v1/reviews", headers=headers, json=payload)
    second = client.post("/api/v1/reviews", headers=headers, json=payload)
    assert second.status_code == 201
    assert second.json() == first.json()
    changed = review_payload(["R3"], [{"concept": "mta_roas", "value": 1.7, "baseline": 1.0}])
    conflict = client.post("/api/v1/reviews", headers=headers, json=changed)
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert client.get("/api/v1/reviews", headers=AUTH).json()["total"] == 1


def test_idempotency_conflict_precedes_changed_payload_business_validation(client: TestClient):
    headers = {**AUTH, "Idempotency-Key": "conflict-before-validation"}
    assert client.post("/api/v1/reviews", headers=headers, json=review_payload(["R7"], [])).status_code == 201
    changed_invalid = review_payload(["R99"], [])
    response = client.post("/api/v1/reviews", headers=headers, json=changed_invalid)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_structured_validation_and_business_errors(client: TestClient):
    no_key = client.post("/api/v1/reviews", headers=AUTH, json=review_payload(["R7"], []))
    assert no_key.status_code == 400
    assert no_key.json()["error"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"
    unknown = client.post(
        "/api/v1/reviews",
        headers={**AUTH, "Idempotency-Key": "unknown"},
        json=review_payload(["R99"], []),
    )
    assert unknown.status_code == 422
    assert unknown.json()["error"]["code"] == "UNKNOWN_RULE"
    duplicate = review_payload(["R3", "R3"], R3_INPUT)
    invalid = client.post("/api/v1/reviews", headers={**AUTH, "Idempotency-Key": "duplicate"}, json=duplicate)
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "VALIDATION_ERROR"
    assert "X-Request-ID" in invalid.headers


def test_restart_persistence(settings):
    with TestClient(create_app(settings)) as first_client:
        created = first_client.post(
            "/api/v1/reviews",
            headers={**AUTH, "Idempotency-Key": "persistent"},
            json=review_payload(["R7"], []),
        ).json()
    with TestClient(create_app(settings)) as second_client:
        loaded = second_client.get(f"/api/v1/reviews/{created['review_id']}", headers=AUTH)
        assert loaded.status_code == 200
        assert loaded.json() == created


def test_broken_ontology_keeps_liveness_but_fails_readiness(settings, tmp_path):
    settings.ontology_path = tmp_path / "missing"
    with TestClient(create_app(settings)) as broken:
        assert broken.get("/health").status_code == 200
        assert broken.get("/ready").status_code == 503
        create = broken.post(
            "/api/v1/reviews",
            headers={"X-API-Key": API_KEY, "Idempotency-Key": "broken"},
            json=review_payload(["R7"], []),
        )
        assert create.status_code == 503
        assert create.json()["error"]["code"] == "ONTOLOGY_UNAVAILABLE"


def test_readiness_fails_when_live_schema_is_incomplete(client: TestClient):
    with client.app.state.database.engine.begin() as connection:
        connection.execute(text("DROP TABLE idempotency_records"))
    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code == 503
    create = client.post(
        "/api/v1/reviews",
        headers={**AUTH, "Idempotency-Key": "schema-unavailable"},
        json=review_payload(["R7"], []),
    )
    assert create.status_code == 503
    assert create.json()["error"]["code"] == "DATABASE_UNAVAILABLE"


def test_unreachable_postgres_keeps_liveness_and_redacts_credentials(settings, caplog):
    settings.database_url = (
        "postgresql+psycopg://secret-user:secret-password@127.0.0.1:1/"
        "missing?connect_timeout=1"
    )
    with TestClient(create_app(settings)) as unavailable:
        health = unavailable.get("/health")
        readiness = unavailable.get("/ready")
        assert health.status_code == 200
        assert readiness.status_code == 503
        serialized = readiness.text
        assert "secret-user" not in serialized
        assert "secret-password" not in serialized
        assert readiness.json()["database_ready"] is False
    assert "secret-user" not in caplog.text
    assert "secret-password" not in caplog.text


def test_readiness_recovers_after_transient_startup_database_failure(settings, monkeypatch):
    import app.main as main_module

    real_initialize = main_module.initialize_database
    attempts = 0

    def flaky_initialize(database_url: str):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("transient database failure")
        return real_initialize(database_url)

    monkeypatch.setattr(main_module, "initialize_database", flaky_initialize)
    with TestClient(create_app(settings)) as recovering:
        assert recovering.get("/health").status_code == 200
        readiness = recovering.get("/ready")
        assert readiness.status_code == 200
        assert readiness.json()["database_ready"] is True
        assert attempts == 2


def test_failed_database_migration_disposes_engine(monkeypatch):
    import app.main as main_module

    closed = False

    class FailingDatabase:
        def __init__(self, database_url: str):
            pass

        def migrate(self):
            raise RuntimeError("migration failed")

        def close(self):
            nonlocal closed
            closed = True

    monkeypatch.setattr(main_module, "Database", FailingDatabase)
    with pytest.raises(RuntimeError, match="migration failed"):
        main_module.initialize_database("sqlite:///:memory:")
    assert closed is True


def test_request_contract_hardening_and_supported_roles():
    base = review_payload(["R7"], [])
    for patch in (
        {"expected_ontology_version": ""},
        {"entity": {"grain": "arbitrary", "id": "bad"}},
        {"context": {"note": "x" * 2_001}},
        {"proposed_action": {"type": "test", "param": {"note": "x" * 2_001}}},
    ):
        with pytest.raises(ValueError):
            ReviewCreate.model_validate(base | patch)
    with pytest.raises(ValueError, match="unsupported API principal role"):
        Settings(api_key_principals="key:principal:tenant:UNKNOWN").principals()

    oversized_context = {f"bucket_{index}": list(range(100)) for index in range(100)}
    with pytest.raises(ValueError, match="maximum total size"):
        ReviewCreate.model_validate(base | {"context": oversized_context})


def test_failed_restore_removes_partial_target(tmp_path):
    invalid_backup = tmp_path / "invalid.db"
    invalid_backup.write_text("not sqlite", encoding="utf-8")
    target = tmp_path / "restored.db"
    result = subprocess.run(
        ["sh", "scripts/restore.sh", str(invalid_backup), str(target)],
        cwd=ONTOLOGY_ROOT.parents[2] / "ontology review api",
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert not target.exists()


def test_canonical_conflict_client_guardrail_and_retired_fixtures_persist(client: TestClient):
    suite = json.loads((ONTOLOGY_ROOT / "assertions" / "story_assertions.json").read_text(encoding="utf-8"))
    assertion_ids = {
        "A-CONFLICT-R1-R2",
        "A-CLIENT-A-R3",
        "A-CLIENT-B-R3",
        "A-G1-PASS",
        "A-G1-BLOCK",
        "A-G2-PASS",
        "A-G2-BLOCK",
        "A-R7-NO-COVERAGE",
    }
    scenarios = [item for item in suite["scenarios"] if item["assertion_id"] in assertion_ids]
    assert {item["assertion_id"] for item in scenarios} == assertion_ids

    for scenario in scenarios:
        payload = {
            "client_id": scenario["client_id"],
            "entity": scenario["entity"],
            "candidate_rules": scenario["rule_refs"],
            "inputs": scenario["inputs"],
            "expected_ontology_version": "v1.1-demo",
        }
        if scenario["category"] == "guardrail":
            payload["proposed_action"] = scenario["expected"]["action"]
        created = client.post(
            "/api/v1/reviews",
            headers={**AUTH, "Idempotency-Key": f"canonical-{scenario['assertion_id']}"},
            json=payload,
        )
        assert created.status_code == 201, (scenario["assertion_id"], created.text)
        body = created.json()
        expected_outcome = scenario["expected"].get("coverage_status")
        if expected_outcome is None:
            expected_outcome = "MATCH" if scenario["expected"]["triggered_rules"] else "NO_COVERAGE"
        assert body["outcome"] == expected_outcome, scenario["assertion_id"]
        expected_disposition = scenario["expected"]["disposition"]
        if scenario["expected"].get("triggered_rules") == ["R3"]:
            expected_disposition = "REVIEW"
        assert body["disposition"] == expected_disposition, scenario["assertion_id"]
        assert body["matched_rules"] == scenario["expected"]["triggered_rules"]
        validated_payload = ReviewCreate.model_validate(payload).model_dump(mode="json")
        assert body["original_request"] == validated_payload
        assert body["ontology_version"] == "v1.1-demo"
        assert len(body["ontology_checksum"]) == 64
        assert body["principal_id"] == "test-agent"
        assert body["tenant"] == "tenant-a"
        assert body["request_id"]
        assert body["record_version"] == 1
        assert body["created_at"]
        if scenario["rule_refs"]:
            assert {item["rule_id"] for item in body["rule_evaluations"]} >= set(scenario["rule_refs"])
        if scenario["guardrail_refs"]:
            evaluations = {item["guardrail_id"]: item for item in body["guardrail_evaluations"]}
            assert set(scenario["guardrail_refs"]) <= set(evaluations)
        fetched = client.get(f"/api/v1/reviews/{body['review_id']}", headers=AUTH)
        assert fetched.status_code == 200
        assert fetched.json() == body


def test_canonical_positive_negative_and_boundary_fixtures_persist(client: TestClient):
    suite = json.loads((ONTOLOGY_ROOT / "assertions" / "story_assertions.json").read_text(encoding="utf-8"))
    scenarios = [
        item
        for item in suite["scenarios"]
        if item["category"] in {"rule_positive", "rule_negative", "rule_boundary"}
    ]
    assert scenarios

    for scenario in scenarios:
        payload = {
            "client_id": scenario["client_id"],
            "entity": scenario["entity"],
            "candidate_rules": scenario["rule_refs"],
            "inputs": scenario["inputs"],
            "expected_ontology_version": "v1.1-demo",
        }
        created = client.post(
            "/api/v1/reviews",
            headers={**AUTH, "Idempotency-Key": f"canonical-{scenario['assertion_id']}"},
            json=payload,
        )
        assert created.status_code == 201, (scenario["assertion_id"], created.text)
        body = created.json()
        expected_rules = scenario["expected"].get("triggered_rules", [])
        assert body["outcome"] == ("MATCH" if expected_rules else "NO_COVERAGE"), scenario["assertion_id"]
        assert body["matched_rules"] == expected_rules, scenario["assertion_id"]
        expected_disposition = scenario["expected"]["disposition"]
        if expected_rules == ["R3"]:
            expected_disposition = "REVIEW"
        assert body["disposition"] == expected_disposition, scenario["assertion_id"]
        assert body["original_request"] == ReviewCreate.model_validate(payload).model_dump(mode="json")
        assert body["ontology_version"] == "v1.1-demo"
        assert len(body["ontology_checksum"]) == 64
        assert body["principal_id"] == "test-agent"
        assert body["tenant"] == "tenant-a"
        assert body["request_id"]
        assert body["record_version"] == 1
        assert body["created_at"]
        assert {item["rule_id"] for item in body["rule_evaluations"]} >= set(scenario["rule_refs"])
        fetched = client.get(f"/api/v1/reviews/{body['review_id']}", headers=AUTH)
        assert fetched.status_code == 200
        assert fetched.json() == body
