from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from fastapi.testclient import TestClient
from jsonschema import Draft7Validator, FormatChecker
from sqlalchemy import text

from app.config import PrincipalConfig
from tests.conftest import AUTH

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PLAN_FIXTURE = PROJECT_ROOT / "tests" / "fixtures" / "plan_a" / "final_plan.demo.json"
REVIEW_SCHEMA = PROJECT_ROOT / "campaign_optimizer" / "schemas" / "ontology_review.schema.json"


def plan_fixture() -> dict:
    return json.loads(PLAN_FIXTURE.read_text(encoding="utf-8"))


def post(client: TestClient, plan: dict, key: str = "plan-review"):
    return client.post(
        "/api/v1/plan-reviews",
        headers={**AUTH, "Idempotency-Key": key},
        json=plan,
    )


def test_pending_campaign_review_is_schema_valid_persisted_and_fetchable(client):
    created = post(client, plan_fixture())
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["source"] == "ONTOLOGY_ENGINE"
    assert body["ontology_version"] == "2.0-campaign-pending"
    assert body["overall_verdict"] == "UNVERIFIED"
    assert body["items"][0]["rule_id"] is None
    schema = json.loads(REVIEW_SCHEMA.read_text(encoding="utf-8"))
    assert not list(Draft7Validator(schema, format_checker=FormatChecker()).iter_errors(body))
    fetched = client.get(f"/api/v1/plan-reviews/{body['review_id']}", headers=AUTH)
    assert fetched.status_code == 200
    assert fetched.json() == body
    assert created.headers["X-Ontology-Checksum"] == fetched.headers["X-Ontology-Checksum"]
    assert len(created.headers["X-Ontology-Checksum"]) == 64


def test_action_changes_cannot_activate_pending_r5(client):
    for index, (action, delta, budget) in enumerate([
        ("increase_budget", 10, 1100),
        ("decrease_budget", -10, 900),
        ("keep_budget", 0, 1000),
    ]):
        plan = plan_fixture()
        plan["plan_id"] = f"plan_action_{index}"
        plan["items"][0].update(
            action=action, delta_pct=delta, recommended_budget=budget
        )
        body = post(client, plan, f"action-{index}").json()
        assert body["overall_verdict"] == "UNVERIFIED"
        assert body["items"][0]["rule_id"] is None


def test_missing_or_null_unapproved_evidence_stays_unverified(client):
    partial = plan_fixture()
    partial["plan_id"] = "plan_partial"
    partial["review_evidence"] = partial["review_evidence"][:1]
    assert post(client, partial, "partial").json()["overall_verdict"] == "UNVERIFIED"
    missing = plan_fixture()
    missing["plan_id"] = "plan_missing"
    missing["review_evidence"] = []
    assert post(client, missing, "missing").json()["overall_verdict"] == "UNVERIFIED"
    null = plan_fixture()
    null["plan_id"] = "plan_null"
    null["review_evidence"][0]["value"] = None
    assert post(client, null, "null").json()["overall_verdict"] == "UNVERIFIED"


def test_idempotency_replays_and_changed_payload_conflicts(client):
    plan = plan_fixture()
    first = post(client, plan, "same")
    second = post(client, plan, "same")
    assert first.status_code == second.status_code == 201
    assert first.json() == second.json()
    changed = deepcopy(plan)
    changed["source_version"] = "2.0"
    conflict = post(client, changed, "same")
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_invalid_binding_and_plan_window_do_not_persist(client):
    invalid = plan_fixture()
    invalid["review_evidence"][0]["entity_id"] = "another-entity"
    assert post(client, invalid, "invalid").status_code == 422
    window = plan_fixture()
    window["period"]["end_date"] = window["period"]["start_date"]
    assert post(client, window, "window").status_code == 422
    with client.app.state.database.engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM plan_reviews")).scalar_one() == 0


def test_unapproved_fact_units_do_not_become_rule_evidence(client):
    plan = plan_fixture()
    plan["review_evidence"][0]["unit"] = "currency"
    response = post(client, plan, "unapproved-unit")
    assert response.status_code == 201
    assert response.json()["overall_verdict"] == "UNVERIFIED"


def test_audit_record_uses_core_version_and_checksum(client):
    response = post(client, plan_fixture(), "audit")
    assert response.status_code == 201
    with client.app.state.database.engine.connect() as connection:
        row = connection.execute(text(
            "SELECT ontology_version, ontology_checksum, original_request_json, "
            "normalized_request_json FROM plan_reviews"
        )).mappings().one()
    assert row["ontology_version"] == "2.0-campaign-pending"
    assert len(row["ontology_checksum"]) == 64
    assert json.loads(row["original_request_json"])["plan_id"] == "plan_demo_001"
    assert json.loads(row["normalized_request_json"])["plan_id"] == "plan_demo_001"


def test_product_path_does_not_import_hannah_generic_evaluator():
    source = (
        PROJECT_ROOT
        / "ontology review api"
        / "app"
        / "services"
        / "plan_review_service.py"
    ).read_text(encoding="utf-8")
    assert "app.services.review_engine" not in source
    assert "ACTION_POLICY" not in source
    assert "ReviewWorkflow" in source
    assert "generate_ontology_review" not in source


def test_tenant_isolation(client):
    body = post(client, plan_fixture(), "tenant").json()
    client.app.state.principals["tenant-b-key"] = PrincipalConfig(
        "tenant-b-key", "tenant-b-reader", "tenant-b", "REVIEWER"
    )
    isolated = client.get(
        f"/api/v1/plan-reviews/{body['review_id']}",
        headers={"X-API-Key": "tenant-b-key"},
    )
    assert isolated.status_code == 404


def test_initial_seed_is_not_accepted_as_final_plan(client):
    seed = {
        "schema_version": "4.0",
        "recommendation_type": "INITIAL_SEED",
        "handoff_status": "READY_FOR_OPTIMIZATION",
        "is_optimized": False,
        "campaigns": [{"campaign_id": "C_DEMO", "campaign_mta_score": 0.5}],
    }
    response = post(client, seed, "seed")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INITIAL_SEED_NOT_REVIEWABLE"
    with client.app.state.database.engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM plan_reviews")).scalar_one() == 0
        assert connection.execute(text("SELECT count(*) FROM model_artifacts")).scalar_one() == 1
