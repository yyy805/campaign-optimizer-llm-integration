"""Verify the formal PostgreSQL cutover, with an optional guarded smoke test.

The default invocation is read-only.  ``--smoke`` creates one uniquely named
plan review, proves idempotent replay and restart persistence, then removes only
the rows carrying that unique plan identity.  A final exact row-count audit must
match the pre-smoke database before the command reports success.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, text

from campaign_optimizer.llm.release_pin import bundle_root
from campaign_optimizer.ontology.publication import (
    load_publication_manifest,
    verify_publication_manifest,
)
from scripts import reconcile_api_alembic_ledger as reconcile


REPO_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = REPO_ROOT / "ontology review api"
MANIFEST_PATH = REPO_ROOT / "campaign_optimizer/ontology/publication_manifest.json"
PLAN_FIXTURE = REPO_ROOT / "tests/fixtures/plan_a/final_plan.demo.json"
EXPECTED_RELEASE = {
    "ontology_version": "2.1-campaign-pending",
    "rule_version": "R5@2.0-campaign-pending",
    "engine_version": "2.1",
    "schema_version": "1.1",
}
SMOKE_CONFIRMATION = "mta_data"
EXPECTED_R5_STATUS = "PENDING_HUMAN_REVIEW"


def validate_smoke_environment() -> str:
    if os.getenv("ALLOW_FORMAL_POSTGRES_SMOKE") != SMOKE_CONFIRMATION:
        raise RuntimeError(
            "formal smoke requires ALLOW_FORMAL_POSTGRES_SMOKE=mta_data"
        )
    backup_reference = os.getenv("MTA_DATA_BACKUP_REFERENCE", "").strip()
    if not backup_reference:
        raise RuntimeError(
            "formal smoke requires a non-empty MTA_DATA_BACKUP_REFERENCE"
        )
    return backup_reference


def verify_release() -> dict[str, object]:
    manifest = load_publication_manifest(MANIFEST_PATH)
    mismatches = {
        key: (manifest.get(key), expected)
        for key, expected in EXPECTED_RELEASE.items()
        if manifest.get(key) != expected
    }
    if mismatches:
        raise RuntimeError(f"release identity mismatch: {mismatches}")
    release_root = bundle_root(manifest)
    verify_publication_manifest(manifest, root=release_root)
    r5_path = release_root / "campaign_optimizer/ontology/rules/R5.json"
    r5_bytes = r5_path.read_bytes()
    r5 = json.loads(r5_bytes.decode("utf-8"))
    if str(r5.get("status", "")).upper() != EXPECTED_R5_STATUS:
        raise RuntimeError(
            f"R5 must remain {EXPECTED_R5_STATUS}, found {r5.get('status')!r}"
        )
    return {
        "manifest": manifest,
        "r5_sha256": hashlib.sha256(r5_bytes).hexdigest(),
    }


def verify_database(connection) -> dict[str, object]:
    errors, details = reconcile.audit(
        connection, formal=True, allow_repairable=False
    )
    if details["api_state"] != "complete":
        errors.append(
            f"post-cutover API schema must be complete, found {details['api_state']}"
        )
    if details["api_ledger"] != [details["api_head"]]:
        errors.append(
            "post-cutover API ledger must contain exactly the API head: "
            f"{details['api_ledger']}"
        )
    if errors:
        raise RuntimeError("post-cutover database audit failed: " + "; ".join(errors))
    return details


def _load_api():
    api_root = str(API_ROOT)
    if api_root in sys.path:
        sys.path.remove(api_root)
    sys.path.insert(0, api_root)
    from fastapi.testclient import TestClient
    from app.config import Settings
    from app.main import create_app

    return TestClient, Settings, create_app


def _cleanup_smoke(connection, *, client_id: str, plan_id: str,
                   principal_id: str, idempotency_key: str) -> None:
    connection.execute(
        text(
            "DELETE FROM idempotency_records WHERE principal_id=:principal "
            "AND endpoint='/api/v1/plan-reviews' AND idempotency_key=:key"
        ),
        {"principal": principal_id, "key": idempotency_key},
    )
    connection.execute(
        text(
            "DELETE FROM plan_reviews WHERE tenant='formal-cutover-smoke' "
            "AND plan_id=:plan_id"
        ),
        {"plan_id": plan_id},
    )
    connection.execute(
        text("DELETE FROM ontology_review_items WHERE client_id=:client AND plan_id=:plan"),
        {"client": client_id, "plan": plan_id},
    )
    connection.execute(
        text("DELETE FROM ontology_reviews WHERE client_id=:client AND plan_id=:plan"),
        {"client": client_id, "plan": plan_id},
    )
    connection.execute(
        text("DELETE FROM plan_items WHERE client_id=:client AND plan_id=:plan"),
        {"client": client_id, "plan": plan_id},
    )
    connection.execute(
        text("DELETE FROM plan_snapshots WHERE client_id=:client AND plan_id=:plan"),
        {"client": client_id, "plan": plan_id},
    )


def run_smoke(raw_url: str, before: dict[str, object]) -> dict[str, str]:
    validate_smoke_environment()
    TestClient, Settings, create_app = _load_api()
    run_id = uuid4().hex
    api_key = f"formal-smoke-key-{run_id}"
    principal_id = f"formal-smoke-{run_id}"
    idempotency_key = f"formal-cutover-{run_id}"
    client_id = "demo_client_001"
    payload = json.loads(PLAN_FIXTURE.read_text(encoding="utf-8"))
    payload["plan_id"] = f"plan_formal_cutover_{run_id}"
    plan_id = payload["plan_id"]
    headers = {"X-API-Key": api_key, "Idempotency-Key": idempotency_key}
    settings = Settings(
        database_url=raw_url,
        ontology_path=REPO_ROOT / "docs/ontology/ontology 概念卡",
        final_plan_schema_path=REPO_ROOT / "campaign_optimizer/schemas/final_plan.schema.json",
        ontology_review_schema_path=REPO_ROOT / "campaign_optimizer/schemas/ontology_review.schema.json",
        api_key_principals=(
            f"{api_key}:{principal_id}:formal-cutover-smoke:SERVICE"
        ),
        plan_review_client_id=client_id,
        docs_enabled=False,
    )
    engine = create_engine(raw_url, pool_pre_ping=True)
    api_review_id: str | None = None
    try:
        with engine.connect() as connection:
            exists = connection.execute(
                text("SELECT 1 FROM clients WHERE client_id=:client"),
                {"client": client_id},
            ).scalar_one_or_none()
            if exists != 1:
                raise RuntimeError(
                    f"required existing client {client_id} is absent; refusing smoke"
                )

        with TestClient(create_app(settings)) as client:
            ready = client.get("/ready")
            if ready.status_code != 200:
                raise RuntimeError(f"/ready failed: {ready.status_code} {ready.text}")
            first = client.post("/api/v1/plan-reviews", headers=headers, json=payload)
            if first.status_code != 201:
                raise RuntimeError(f"create failed: {first.status_code} {first.text}")
            api_review_id = first.json()["review_id"]
            replay = client.post("/api/v1/plan-reviews", headers=headers, json=payload)
            if replay.status_code != 201 or replay.json() != first.json():
                raise RuntimeError("idempotent replay did not return the original response")

        with TestClient(create_app(settings)) as restarted:
            if restarted.get("/ready").status_code != 200:
                raise RuntimeError("/ready failed after application restart")
            persisted = restarted.get(
                f"/api/v1/plan-reviews/{api_review_id}",
                headers={"X-API-Key": api_key},
            )
            if persisted.status_code != 200 or persisted.json()["plan_id"] != plan_id:
                raise RuntimeError("review persistence/readback failed after restart")
    finally:
        try:
            with engine.begin() as connection:
                _cleanup_smoke(
                    connection,
                    client_id=client_id,
                    plan_id=plan_id,
                    principal_id=principal_id,
                    idempotency_key=idempotency_key,
                )
            with engine.connect() as connection:
                after = verify_database(connection)
            if dict(after["row_counts"]) != dict(before["row_counts"]):
                raise RuntimeError(
                    "formal smoke cleanup did not restore exact pre-smoke row counts"
                )
        finally:
            engine.dispose()
    return {"plan_id": plan_id, "review_id": str(api_review_id)}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify the completed formal PostgreSQL cutover."
    )
    parser.add_argument(
        "--smoke", action="store_true",
        help="run guarded create/replay/read/restart smoke and exact cleanup",
    )
    args = parser.parse_args()
    raw_url = os.getenv("FORMAL_POSTGRES_URL")
    if not raw_url:
        print("FORMAL_POSTGRES_URL is not set", file=sys.stderr)
        return 2
    try:
        reconcile.validate_target_url(
            raw_url, variable="FORMAL_POSTGRES_URL", formal=True
        )
        release = verify_release()
        engine = create_engine(raw_url, pool_pre_ping=True)
        try:
            with engine.connect() as connection:
                database = verify_database(connection)
        finally:
            engine.dispose()
        manifest = dict(release["manifest"])
        print("release audit: PASS")
        print(f"ontology version: {manifest['ontology_version']}")
        print(f"release checksum: {manifest['package_checksum']}")
        print(f"R5 status/hash: {EXPECTED_R5_STATUS}/{release['r5_sha256']}")
        reconcile.print_report([], database)
        print("SQLite data migration: NOT REQUIRED (no real source business data)")
        if args.smoke:
            result = run_smoke(raw_url, database)
            print(
                "formal smoke: PASS "
                f"(plan={result['plan_id']}, review={result['review_id']}, cleanup=exact)"
            )
        else:
            print("POST_CUTOVER_READ_ONLY: PASS")
        return 0
    except Exception as exc:
        print(f"CUTOVER VERIFICATION FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
