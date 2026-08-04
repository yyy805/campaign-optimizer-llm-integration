from __future__ import annotations

import json
import shutil
import sys
from copy import deepcopy
from pathlib import Path

from fastapi.testclient import TestClient
from jsonschema import Draft7Validator, FormatChecker
from sqlalchemy import text

from tests.conftest import AUTH
from app.config import PrincipalConfig
from app.main import create_app


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CAMPAIGN_ROOT = PROJECT_ROOT / "docs" / "campaign-optimizer-llm-integration-main"
PLAN_FIXTURE = CAMPAIGN_ROOT / "tests" / "fixtures" / "plan_a" / "final_plan.demo.json"
REVIEW_SCHEMA = CAMPAIGN_ROOT / "campaign_optimizer" / "schemas" / "ontology_review.schema.json"


def plan_fixture() -> dict:
    return json.loads(PLAN_FIXTURE.read_text(encoding="utf-8"))


def post(client: TestClient, plan: dict, key: str = "plan-review"):
    return client.post(
        "/api/v1/plan-reviews",
        headers={**AUTH, "Idempotency-Key": key},
        json=plan,
    )


def test_demo_plan_returns_schema_valid_canonical_conflict_and_persists(client: TestClient):
    created = post(client, plan_fixture())
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["source"] == "ONTOLOGY_ENGINE"
    assert body["ontology_version"] == "v1.1-demo"
    assert body["plan_id"] == "plan_demo_001"
    assert body["overall_verdict"] == "CONFLICT"
    r5 = next(item for item in body["items"] if item["rule_id"] == "R5")
    assert r5["verdict"] == "CONFLICT"
    assert r5["matched_fact_ids"] == ["review_fact_001", "review_fact_002", "review_fact_003"]
    schema = json.loads(REVIEW_SCHEMA.read_text(encoding="utf-8"))
    assert not list(Draft7Validator(schema, format_checker=FormatChecker()).iter_errors(body))
    fetched = client.get(f"/api/v1/plan-reviews/{body['review_id']}", headers=AUTH)
    assert fetched.status_code == 200
    assert fetched.json() == body
    assert created.headers["X-Ontology-Checksum"] == fetched.headers["X-Ontology-Checksum"]
    assert len(created.headers["X-Ontology-Checksum"]) == 64


def test_supported_and_not_applicable_projection(client: TestClient):
    supported = plan_fixture()
    supported["items"][0].update({"action": "decrease_budget", "delta_pct": -10, "recommended_budget": 900})
    support_body = post(client, supported, "support").json()
    assert next(item for item in support_body["items"] if item["rule_id"] == "R5")["verdict"] == "SUPPORT"

    neutral = plan_fixture()
    neutral["items"][0].update({"action": "keep_budget", "delta_pct": 0, "recommended_budget": 1000})
    neutral_body = post(client, neutral, "neutral").json()
    assert next(item for item in neutral_body["items"] if item["rule_id"] == "R5")["verdict"] == "NOT_APPLICABLE"


def test_partial_evidence_is_insufficient_and_no_matching_rule_is_unverified(client: TestClient):
    partial = plan_fixture()
    partial["review_evidence"] = partial["review_evidence"][:1]
    body = post(client, partial, "partial").json()
    assert body["overall_verdict"] == "INSUFFICIENT_EVIDENCE"
    r5 = next(item for item in body["items"] if item["rule_id"] == "R5")
    assert r5["missing_evidence"] == ["spend_share", "attribution_divergence"]

    uncovered = plan_fixture()
    uncovered["review_evidence"] = []
    body = post(client, uncovered, "uncovered").json()
    assert body["overall_verdict"] == "INSUFFICIENT_EVIDENCE"
    assert {item["rule_id"] for item in body["items"]} == {"R3", "R5"}


def test_invalid_binding_does_not_persist_and_idempotency_is_immutable(client: TestClient):
    invalid = plan_fixture()
    invalid["review_evidence"][0]["entity_id"] = "another-entity"
    response = post(client, invalid, "invalid")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    plan = plan_fixture()
    first = post(client, plan, "same")
    second = post(client, plan, "same")
    assert second.status_code == 201
    assert second.json() == first.json()
    changed = deepcopy(plan)
    changed["source_version"] = "2.0"
    conflict = post(client, changed, "same")
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_campaign_ontology_copy_is_not_a_runtime_input(client: TestClient):
    runtime = client.app.state.ontology
    assert runtime.root == PROJECT_ROOT / "docs" / "ontology" / "ontology 概念卡"
    assert "attribution_consistency_status" in runtime.concepts
    assert "prediction_confidence" not in runtime.concepts


def test_null_and_completely_missing_evidence_are_insufficient(client: TestClient):
    plan = plan_fixture()
    plan["review_evidence"][0]["value"] = None
    body = post(client, plan, "null-evidence").json()
    r5 = next(item for item in body["items"] if item["rule_id"] == "R5")
    assert "contribution_share" in r5["missing_evidence"]
    assert "review_fact_001" not in r5["matched_fact_ids"]

    plan = plan_fixture()
    plan["review_evidence"] = []
    body = post(client, plan, "all-missing").json()
    assert body["overall_verdict"] == "INSUFFICIENT_EVIDENCE"
    assert {item["rule_id"] for item in body["items"]} == {"R3", "R5"}


def test_account_plan_with_no_canonical_rule_is_unverified(client: TestClient):
    plan = plan_fixture()
    plan["items"][0]["entity_type"] = "account"
    for fact in plan["decision_evidence"]:
        fact["entity_type"] = "account"
    plan["review_evidence"] = []
    body = post(client, plan, "account-unverified").json()
    assert body["overall_verdict"] == "UNVERIFIED"
    assert body["items"][0]["rule_id"] is None


def test_invalid_unit_period_baseline_and_plan_window_are_rejected(client: TestClient):
    cases = []
    unit = plan_fixture()
    unit["review_evidence"][0]["unit"] = "currency"
    cases.append((unit, "UNIT_MISMATCH"))
    period = plan_fixture()
    period["review_evidence"][0]["period"] = "one_second"
    cases.append((period, "PERIOD_MISMATCH"))
    window = plan_fixture()
    window["period"]["end_date"] = window["period"]["start_date"]
    cases.append((window, "VALIDATION_ERROR"))
    baseline = plan_fixture()
    baseline["review_evidence"] = [{
        "fact_id": "review_fact_r3", "plan_item_id": "plan_item_001",
        "entity_type": "channel", "entity_id": "Sponsored Products", "name": "mta_roas",
        "value": 0, "baseline_value": -1, "baseline_source": "test",
        "baseline_period": "current_snapshot", "unit": "ratio",
        "period": "current_snapshot", "source": "test", "scope": "ontology_review",
    }]
    cases.append((baseline, "METRIC_OUT_OF_RANGE"))
    for index, (payload, code) in enumerate(cases):
        response = post(client, payload, f"semantic-{index}")
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == code
    with client.app.state.database.engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM plan_reviews")).scalar_one() == 0


def test_external_schema_rejects_numeric_strings_and_long_fact_strings(client: TestClient):
    numeric = plan_fixture()
    numeric["items"][0]["current_budget"] = "1000"
    assert post(client, numeric, "numeric-string").json()["error"]["code"] == "FINAL_PLAN_SCHEMA_INVALID"
    long_string = plan_fixture()
    long_string["review_evidence"][0]["value"] = "x" * 501
    assert post(client, long_string, "long-string").json()["error"]["code"] == "FINAL_PLAN_SCHEMA_INVALID"


def test_replay_precedes_current_client_check_and_audit_is_complete(client: TestClient):
    raw = plan_fixture()
    raw["plan_id"] = "plan_" + "x" * 400
    first = post(client, raw, "audit-key")
    assert first.status_code == 201
    client.app.state.settings.plan_review_client_id = "temporarily_missing"
    replay = post(client, raw, "audit-key")
    assert replay.status_code == 201
    assert replay.json() == first.json()
    assert replay.headers["X-Ontology-Checksum"] == first.headers["X-Ontology-Checksum"]
    with client.app.state.database.engine.connect() as connection:
        row = connection.execute(text(
            "SELECT plan_id, tenant, principal_id, request_id, ontology_version, ontology_checksum, "
            "original_request_json, normalized_request_json FROM plan_reviews"
        )).mappings().one()
        assert row["plan_id"] == raw["plan_id"]
        assert row["tenant"] == "tenant-a" and row["principal_id"] == "test-agent"
        assert row["request_id"] and row["ontology_version"] == "v1.1-demo"
        assert len(row["ontology_checksum"]) == 64
        assert json.loads(row["original_request_json"])["period"]["start_date"] == "2026-08-01"
        assert json.loads(row["normalized_request_json"])["period"]["start_date"] == "2026-08-01"
        assert isinstance(json.loads(row["original_request_json"])["items"][0]["current_budget"], int)
        assert isinstance(json.loads(row["normalized_request_json"])["items"][0]["current_budget"], float)
        assert connection.execute(text("SELECT length(review_id) FROM idempotency_records")).scalar_one() == 39


def test_real_response_passes_downstream_canonical_compatibility_gate(client: TestClient):
    response = post(client, plan_fixture(), "downstream")
    assert response.status_code == 201
    sys.path.insert(0, str(CAMPAIGN_ROOT))
    try:
        from campaign_optimizer.llm.request_builder import RequestBuilder
        from campaign_optimizer.llm.retriever import LocalRuleRetriever
        canonical = PROJECT_ROOT / "docs" / "ontology" / "ontology 概念卡"
        retriever = LocalRuleRetriever(
            rules_dir=canonical / "rules", rule_schema_path=canonical / "schemas" / "rule.schema.json",
        )
        artifacts = RequestBuilder(retriever, rules_dir=canonical / "rules").build(
            plan_fixture(), response.json(), mode="initial_render",
            question="Explain the validated ontology review.", resolved_intent="EXPLAIN_REVIEW",
        )
        assert artifacts.context["review_context"] == response.json()
    finally:
        sys.path.remove(str(CAMPAIGN_ROOT))


def test_multiple_items_rules_and_tenant_isolation(client: TestClient):
    plan = plan_fixture()
    second = deepcopy(plan["items"][0])
    second.update({"plan_item_id": "plan_item_002", "entity_type": "campaign", "entity_id": "campaign-2"})
    plan["items"].append(second)
    plan["review_evidence"].extend([
        {"fact_id": "review_fact_acos", "plan_item_id": "plan_item_002", "entity_type": "campaign", "entity_id": "campaign-2", "name": "acos", "value": .5, "baseline_value": .3, "baseline_source": "test", "baseline_period": "current_14_days", "unit": "ratio", "period": "current_14_days", "source": "test", "scope": "ontology_review"},
        {"fact_id": "review_fact_ctr", "plan_item_id": "plan_item_002", "entity_type": "campaign", "entity_id": "campaign-2", "name": "ctr", "value": .01, "baseline_value": .02, "baseline_source": "test", "baseline_period": "current_14_days", "unit": "ratio", "period": "current_14_days", "source": "test", "scope": "ontology_review"},
        {"fact_id": "review_fact_growth", "plan_item_id": "plan_item_002", "entity_type": "campaign", "entity_id": "campaign-2", "name": "impressions_growth", "value": .3, "unit": "ratio", "period": "current_7_days", "source": "test", "scope": "ontology_review"},
    ])
    created = post(client, plan, "multi")
    assert created.status_code == 201, created.text
    body = created.json()
    assert {item["plan_item_id"] for item in body["items"]} == {"plan_item_001", "plan_item_002"}
    assert {item["rule_id"] for item in body["items"] if item["plan_item_id"] == "plan_item_002"} >= {"R1", "R2"}
    client.app.state.principals["tenant-b-key"] = PrincipalConfig("tenant-b-key", "tenant-b-reader", "tenant-b", "REVIEWER")
    isolated = client.get(
        f"/api/v1/plan-reviews/{body['review_id']}", headers={"X-API-Key": "tenant-b-key"},
    )
    assert isolated.status_code == 404


def test_budget_overflow_and_database_lengths_are_guarded(client: TestClient):
    plan = plan_fixture()
    plan["items"][0].update({"current_budget": 1e308, "delta_pct": 1000, "recommended_budget": 1e308})
    response = post(client, plan, "overflow")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    columns = {item["name"]: item for item in __import__("sqlalchemy").inspect(client.app.state.database.engine).get_columns("idempotency_records")}
    assert columns["review_id"]["type"].length >= 64


def test_modified_canonical_package_fails_readiness(settings, tmp_path: Path):
    canonical = PROJECT_ROOT / "docs" / "ontology" / "ontology 概念卡"
    altered = tmp_path / "altered-ontology"
    shutil.copytree(canonical, altered)
    rule = altered / "rules" / "R5.json"
    content = json.loads(rule.read_text(encoding="utf-8"))
    content["known_limitations"].append("checksum mutation used by the startup test")
    rule.write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
    settings.ontology_path = altered
    with TestClient(create_app(settings)) as guarded:
        readiness = guarded.get("/ready")
        assert readiness.status_code == 503
        assert readiness.json()["ontology_ready"] is False
        assert {item["component"] for item in readiness.json()["errors"]} == {"ontology"}
