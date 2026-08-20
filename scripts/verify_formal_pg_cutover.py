"""Verify the formal PostgreSQL cutover, with an optional guarded smoke test.

The default invocation is read-only. ``--smoke`` requires an operator-supplied
Run ID, creates one uniquely named plan review, proves idempotent replay and
fresh-app-instance persistence, then removes only rows carrying that identity.
Run-scoped residue must be zero; database-wide count drift is a concurrency
alarm. This is a database gate, not an ECS/systemd restart test.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

from sqlalchemy import bindparam, create_engine, text

from campaign_optimizer.llm.release_pin import bundle_root, load_verified_manifests
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
    "source_commit": "5e7bb6a631342f99d1d3c6cff6be736f88aede34",
    "package_checksum": "f10e335d47387b527044e9429a2b316d99fcda7af0ae2495ba0ff138eafa9d0c",
    "ontology_version": "2.1-campaign-pending",
    "rule_version": "R5@2.0-campaign-pending",
    "engine_version": "2.1",
    "schema_version": "1.1",
}
EXPECTED_HISTORY = {
    (
        "a83ff2b4f63a7e24da276a034b39d3360b6fccc3",
        "626cfbdedc954f41c7a335cf6178886c8c5cc3c71b2c1b7400ed6f87595d3513",
        "2.0-campaign-pending",
        "R5@2.0-campaign-pending",
        "2.0",
        "1.1",
    )
}
SMOKE_CONFIRMATION = "mta_data"
MAINTENANCE_CONFIRMATION = "mta_data"
EXPECTED_R5_STATUS = "PENDING_HUMAN_REVIEW"
EXPECTED_R5_SHA256 = "eced62fd789b0fb903a50722fe4600ea06906a357af88c84f023122292eb7b64"
RUN_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{7,63}$")
FORMAL_SMOKE_CLIENT_ID = "cutover-client-001"
# Must differ from Database.MIGRATION_LOCK_ID/reconcile.LOCK_ID. Each TestClient
# startup takes the migration lock on its own connection; sharing that ID here
# would deadlock the verifier against the application it is testing.
VERIFIER_LOCK_ID = reconcile.LOCK_ID + 104_729


def validate_smoke_environment() -> str:
    if os.getenv("ALLOW_FORMAL_POSTGRES_SMOKE") != SMOKE_CONFIRMATION:
        raise RuntimeError(
            "formal smoke requires ALLOW_FORMAL_POSTGRES_SMOKE=mta_data"
        )
    if os.getenv("ALLOW_FORMAL_POSTGRES_MAINTENANCE") != MAINTENANCE_CONFIRMATION:
        raise RuntimeError(
            "formal smoke/cleanup requires "
            "ALLOW_FORMAL_POSTGRES_MAINTENANCE=mta_data to acknowledge writers are quiesced"
        )
    if os.getenv("MTA_DATA_BACKUP_CONFIRMED") != "1":
        raise RuntimeError("formal smoke/cleanup requires MTA_DATA_BACKUP_CONFIRMED=1")
    backup_reference = os.getenv("MTA_DATA_BACKUP_REFERENCE", "").strip()
    if not backup_reference:
        raise RuntimeError(
            "formal smoke requires a non-empty MTA_DATA_BACKUP_REFERENCE"
        )
    lowered = backup_reference.lower()
    if any(token in lowered for token in ("<", ">", "placeholder", "example", "todo", "snapshot-reference")):
        raise RuntimeError("MTA_DATA_BACKUP_REFERENCE must be a real backup identifier, not a placeholder")
    return backup_reference


def validate_run_id(run_id: str) -> str:
    if not RUN_ID_PATTERN.fullmatch(run_id):
        raise ValueError(
            "run ID must be 8-64 lowercase letters, digits, or hyphens and start alphanumeric"
        )
    return run_id


def smoke_identity(run_id: str) -> dict[str, str]:
    validate_run_id(run_id)
    return {
        "run_id": run_id,
        "api_key": f"formal-smoke-key-{run_id}",
        "principal_id": f"formal-smoke-{run_id}",
        "idempotency_key": f"formal-cutover-{run_id}",
        "client_id": FORMAL_SMOKE_CLIENT_ID,
        "plan_id": f"plan_formal_cutover_{run_id}",
    }


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
    verified = load_verified_manifests()
    identities = {
        tuple(item.get(key) for key in (
            "source_commit", "package_checksum", "ontology_version",
            "rule_version", "engine_version", "schema_version",
        ))
        for item in verified.values()
    }
    if not EXPECTED_HISTORY.issubset(identities):
        raise RuntimeError("approved archived 2.0 release is missing or fails frozen verification")
    r5_path = release_root / "campaign_optimizer/ontology/rules/R5.json"
    r5_bytes = r5_path.read_bytes()
    r5 = json.loads(r5_bytes.decode("utf-8"))
    if str(r5.get("status", "")).upper() != EXPECTED_R5_STATUS:
        raise RuntimeError(
            f"R5 must remain {EXPECTED_R5_STATUS}, found {r5.get('status')!r}"
        )
    r5_sha256 = hashlib.sha256(r5_bytes).hexdigest()
    if r5_sha256 != EXPECTED_R5_SHA256:
        raise RuntimeError(
            f"R5 bytes changed: expected {EXPECTED_R5_SHA256}, found {r5_sha256}"
        )
    return {
        "manifest": manifest,
        "r5_sha256": r5_sha256,
        "verified_manifest_count": len(verified),
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


def _smoke_residue_counts(
    connection, identity: dict[str, str], *, known_review_ids: tuple[str, ...] | None = None
) -> dict[str, int]:
    params = {
        "client": identity["client_id"], "plan": identity["plan_id"],
        "principal": identity["principal_id"], "key": identity["idempotency_key"],
    }
    queries = {
        "idempotency_records": (
            "SELECT count(*) FROM idempotency_records WHERE principal_id=:principal "
            "AND endpoint='/api/v1/plan-reviews' AND idempotency_key=:key"
        ),
        "plan_reviews": (
            "SELECT count(*) FROM plan_reviews WHERE tenant='formal-cutover-smoke' "
            "AND plan_id=:plan"
        ),
        "plan_snapshots": "SELECT count(*) FROM plan_snapshots WHERE client_id=:client AND plan_id=:plan",
        "plan_items": "SELECT count(*) FROM plan_items WHERE client_id=:client AND plan_id=:plan",
        "ontology_reviews": "SELECT count(*) FROM ontology_reviews WHERE client_id=:client AND plan_id=:plan",
        "ontology_review_items": "SELECT count(*) FROM ontology_review_items WHERE client_id=:client AND plan_id=:plan",
        "plan_decision_events": "SELECT count(*) FROM plan_decision_events WHERE client_id=:client AND plan_id=:plan",
    }
    counts = {
        table: int(connection.execute(text(sql), params).scalar_one())
        for table, sql in queries.items()
    }
    if known_review_ids is None:
        feedback = connection.execute(
            text(
                "SELECT count(*) FROM feedback_events f WHERE f.client_id=:client AND EXISTS "
                "(SELECT 1 FROM ontology_reviews r WHERE r.client_id=:client "
                "AND r.plan_id=:plan AND r.review_id=f.review_id)"
            ),
            params,
        ).scalar_one()
    elif known_review_ids:
        statement = text(
            "SELECT count(*) FROM feedback_events WHERE client_id=:client "
            "AND review_id IN :review_ids"
        ).bindparams(bindparam("review_ids", expanding=True))
        feedback = connection.execute(
            statement, {"client": identity["client_id"], "review_ids": known_review_ids}
        ).scalar_one()
    else:
        feedback = 0
    counts["feedback_events"] = int(feedback)
    return counts


def _cleanup_smoke(connection, identity: dict[str, str]) -> tuple[str, ...]:
    client_id = identity["client_id"]
    plan_id = identity["plan_id"]
    principal_id = identity["principal_id"]
    idempotency_key = identity["idempotency_key"]
    review_ids = tuple(connection.execute(
        text("SELECT review_id FROM ontology_reviews WHERE client_id=:client AND plan_id=:plan FOR UPDATE"),
        {"client": client_id, "plan": plan_id},
    ).scalars())
    if review_ids:
        statement = text(
            "DELETE FROM feedback_events WHERE client_id=:client AND review_id IN :review_ids"
        ).bindparams(bindparam("review_ids", expanding=True))
        connection.execute(
            statement, {"client": client_id, "review_ids": review_ids}
        )
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
        text("DELETE FROM plan_decision_events WHERE client_id=:client AND plan_id=:plan"),
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
    return review_ids


def _acquire_verifier_lock(connection, *, context: str, timeout_seconds: float = 10.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while True:
        acquired = bool(connection.execute(
            text("SELECT pg_try_advisory_lock(:lock_id)"),
            {"lock_id": VERIFIER_LOCK_ID},
        ).scalar_one())
        connection.commit()
        if acquired:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"timed out waiting for formal verifier lock ({context}); "
                "another migration/smoke/cleanup may be active"
            )
        time.sleep(0.25)


def _release_verifier_lock(connection) -> None:
    connection.execute(
        text("SELECT pg_advisory_unlock(:lock_id)"), {"lock_id": VERIFIER_LOCK_ID}
    )
    connection.commit()


def _raise_smoke_failures(primary: BaseException | None, cleanup: BaseException | None) -> None:
    if primary is not None and cleanup is not None:
        group = ExceptionGroup if isinstance(primary, Exception) and isinstance(cleanup, Exception) else BaseExceptionGroup
        raise group("database persistence smoke and cleanup both failed", [primary, cleanup])
    if primary is not None:
        raise primary
    if cleanup is not None:
        raise cleanup


def _group_errors(label: str, errors: list[BaseException]) -> BaseException | None:
    if not errors:
        return None
    if len(errors) == 1:
        return errors[0]
    group = ExceptionGroup if all(isinstance(error, Exception) for error in errors) else BaseExceptionGroup
    return group(label, errors)


def cleanup_run(raw_url: str, run_id: str) -> dict[str, int]:
    validate_smoke_environment()
    identity = smoke_identity(run_id)
    engine = create_engine(raw_url, pool_pre_ping=True)
    lock_connection = engine.connect()
    locked = False
    primary_error: BaseException | None = None
    release_error: BaseException | None = None
    residue: dict[str, int] = {}
    try:
        _acquire_verifier_lock(lock_connection, context=f"cleanup run_id={run_id}")
        locked = True
        with engine.begin() as connection:
            review_ids = _cleanup_smoke(connection, identity)
        with engine.connect() as connection:
            residue = _smoke_residue_counts(
                connection, identity, known_review_ids=review_ids
            )
            verify_database(connection)
        remaining = {name: count for name, count in residue.items() if count}
        if remaining:
            raise RuntimeError(f"cleanup left run-scoped rows: {remaining}")
    except BaseException as exc:
        primary_error = exc
    finally:
        resource_errors: list[BaseException] = []
        try:
            if locked:
                _release_verifier_lock(lock_connection)
        except BaseException as exc:
            resource_errors.append(exc)
        try:
            lock_connection.close()
        except BaseException as exc:
            resource_errors.append(exc)
        try:
            engine.dispose()
        except BaseException as exc:
            resource_errors.append(exc)
        release_error = _group_errors("formal cleanup resource finalization failed", resource_errors)
    _raise_smoke_failures(primary_error, release_error)
    return residue


def run_smoke(raw_url: str, before: dict[str, object], run_id: str) -> dict[str, str]:
    validate_smoke_environment()
    identity = smoke_identity(run_id)
    TestClient, Settings, create_app = _load_api()
    api_key = identity["api_key"]
    principal_id = identity["principal_id"]
    idempotency_key = identity["idempotency_key"]
    client_id = identity["client_id"]
    payload = json.loads(PLAN_FIXTURE.read_text(encoding="utf-8"))
    payload["plan_id"] = identity["plan_id"]
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
    lock_connection = engine.connect()
    locked = False
    api_review_id: str | None = None
    primary_error: BaseException | None = None
    cleanup_error: BaseException | None = None
    cleanup_needed = False
    print(f"SMOKE_RUN_ID: {run_id}")
    print(f"RECOVERY: rerun with --cleanup-run-id {run_id}")
    try:
        _acquire_verifier_lock(lock_connection, context=f"smoke run_id={run_id}")
        locked = True
        exists = lock_connection.execute(
            text("SELECT 1 FROM clients WHERE client_id=:client"),
            {"client": client_id},
        ).scalar_one_or_none()
        if exists != 1:
            raise RuntimeError(
                f"required existing client {client_id} is absent; refusing smoke"
            )
        residue = _smoke_residue_counts(lock_connection, identity)
        if any(residue.values()):
            raise RuntimeError(
                f"run ID already has residue {residue}; use --cleanup-run-id {run_id} first"
            )
        lock_connection.rollback()
        cleanup_needed = True

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
    except BaseException as exc:
        primary_error = exc
    finally:
        try:
            if cleanup_needed:
                with engine.begin() as connection:
                    review_ids = _cleanup_smoke(connection, identity)
                with engine.connect() as connection:
                    after = verify_database(connection)
                    residue = _smoke_residue_counts(
                        connection, identity, known_review_ids=review_ids
                    )
                remaining = {name: count for name, count in residue.items() if count}
                if remaining:
                    raise RuntimeError(f"cleanup left run-scoped rows: {remaining}")
                if dict(after["row_counts"]) != dict(before["row_counts"]):
                    raise RuntimeError(
                        "database-wide row counts changed while maintenance was acknowledged; "
                        "run-scoped cleanup is complete but concurrent/unexpected writes occurred"
                    )
        except BaseException as exc:
            cleanup_error = exc
        finally:
            resource_errors: list[BaseException] = []
            try:
                if locked:
                    _release_verifier_lock(lock_connection)
            except BaseException as exc:
                resource_errors.append(exc)
            try:
                lock_connection.close()
            except BaseException as exc:
                resource_errors.append(exc)
            try:
                engine.dispose()
            except BaseException as exc:
                resource_errors.append(exc)
            finalization_error = _group_errors(
                "formal smoke resource finalization failed", resource_errors
            )
            if finalization_error is not None:
                cleanup_error = _group_errors(
                    "formal smoke cleanup/finalization failed",
                    [error for error in (cleanup_error, finalization_error) if error is not None],
                )
    _raise_smoke_failures(primary_error, cleanup_error)
    return {"plan_id": plan_id, "review_id": str(api_review_id)}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify the completed formal PostgreSQL cutover."
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--smoke", action="store_true",
        help="run guarded create/replay/read/app-recreation persistence smoke",
    )
    parser.add_argument("--run-id", help="required deterministic identity for --smoke")
    modes.add_argument(
        "--cleanup-run-id",
        help="remove and verify only rows belonging to a prior deterministic smoke run",
    )
    args = parser.parse_args()
    if args.smoke and not args.run_id:
        parser.error("--smoke requires --run-id")
    if args.run_id and not args.smoke:
        parser.error("--run-id is only valid with --smoke")
    if args.cleanup_run_id is not None:
        try:
            validate_run_id(args.cleanup_run_id)
        except ValueError as exc:
            parser.error(str(exc))
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
        print(f"verified current/history manifests: {release['verified_manifest_count']}")
        print(f"R5 status/hash: {EXPECTED_R5_STATUS}/{release['r5_sha256']}")
        reconcile.print_report([], database)
        print(
            "SQLite transfer scope: NOT REQUIRED BY APPROVED SPEC "
            "(this command does not discover runtime SQLite files)"
        )
        if args.cleanup_run_id:
            cleanup_run(raw_url, args.cleanup_run_id)
            print(f"RUN_SCOPED_CLEANUP: PASS (run_id={args.cleanup_run_id})")
            print("FORMAL_DATABASE_CUTOVER_VERIFIED: NOT ESTABLISHED BY CLEANUP")
            return 0
        if args.smoke:
            result = run_smoke(raw_url, database, validate_run_id(args.run_id))
            print(
                "database persistence/app-recreation smoke: PASS "
                f"(run_id={args.run_id}, plan={result['plan_id']}, "
                f"review={result['review_id']}, run_scoped_cleanup=zero)"
            )
            print("FORMAL_DATABASE_CUTOVER_VERIFIED: PASS")
            print("ECS_DEPLOYED_SERVICE_CUTOVER: NOT TESTED BY THIS COMMAND")
        else:
            print("POST_CUTOVER_READ_ONLY: PASS")
        return 0
    except BaseException as exc:
        def messages(error: BaseException) -> list[str]:
            if isinstance(error, BaseExceptionGroup):
                return [message for child in error.exceptions for message in messages(child)]
            return [f"{type(error).__name__}: {error}"]

        print("CUTOVER VERIFICATION FAILED:", file=sys.stderr)
        for message in messages(exc):
            print(f"- {message}", file=sys.stderr)
        return 130 if any("KeyboardInterrupt:" in item for item in messages(exc)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
