"""Audit and safely adopt an existing Ontology Review API schema.

The API used the shared ``alembic_version`` table in early deployments.  It now
uses ``api_alembic_version`` so it cannot collide with the repository-level
ontology migrations.  An existing database can therefore contain the complete
API schema while the new API ledger is absent or empty.

The default mode is read-only.  ``--apply`` only records the API head after a
strict schema audit succeeds.  It never creates, drops, or alters business
tables and never modifies the legacy ``alembic_version`` table.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Connection, make_url


REPO_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = REPO_ROOT / "ontology review api"
API_LEDGER = "api_alembic_version"
LEGACY_LEDGER = "alembic_version"
LOCK_ID = 6_038_024_217_952_623_953
FORMAL_DATABASE = "mta_data"
ROOT_HEAD = "7b8f3d1a2c4e"
ROOT_TABLES = {
    "concepts", "rules", "clients", "diagnoses", "execution_log",
    "model_artifacts", "plan_snapshots", "plan_items", "ontology_reviews",
    "ontology_review_items", "feedback_events", "rule_confidence_states",
    "plan_decision_events",
}

EXPECTED_COLUMNS = {
    "reviews": {
        "id",
        "schema_version",
        "tenant",
        "client_id",
        "entity_json",
        "original_request_json",
        "outcome",
        "disposition",
        "reason",
        "matched_rules_json",
        "winner_rule",
        "suppressed_rules_json",
        "action_json",
        "rule_evaluations_json",
        "guardrail_evaluations_json",
        "evidence_refs_json",
        "evidence_status",
        "ontology_version",
        "ontology_checksum",
        "status",
        "principal_id",
        "request_id",
        "record_version",
        "created_at",
        "updated_at",
    },
    "idempotency_records": {
        "id",
        "principal_id",
        "endpoint",
        "idempotency_key",
        "request_hash",
        "status_code",
        "response_json",
        "review_id",
        "created_at",
    },
    "plan_reviews": {
        "id",
        "plan_id",
        "tenant",
        "client_id",
        "original_request_json",
        "normalized_request_json",
        "response_json",
        "ontology_version",
        "ontology_checksum",
        "principal_id",
        "request_id",
        "created_at",
    },
}

EXPECTED_INDEXES = {
    "reviews": {"ix_reviews_tenant_created", "ix_reviews_filters"},
    "idempotency_records": {"ix_idempotency_created"},
    "plan_reviews": {"ix_plan_reviews_plan_id", "ix_plan_reviews_tenant_created"},
}


def api_head() -> str:
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "migrations"))
    heads = ScriptDirectory.from_config(config).get_heads()
    if len(heads) != 1:
        raise RuntimeError(f"expected exactly one API migration head, found {heads}")
    return heads[0]


def ledger_values(connection: Connection, table: str, tables: set[str]) -> list[str] | None:
    if table not in tables:
        return None
    rows = connection.execute(text(f'SELECT version_num FROM "{table}"')).scalars()
    return sorted(str(value) for value in rows)


def audit(connection: Connection, *, formal: bool = False) -> tuple[list[str], dict[str, object]]:
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    errors: list[str] = []

    database = connection.execute(text("SELECT current_database()" )).scalar_one()
    if formal:
        if database != FORMAL_DATABASE:
            errors.append(f"formal mode requires database {FORMAL_DATABASE}, found {database}")
        missing_root = ROOT_TABLES - tables
        if missing_root:
            errors.append(f"missing root tables: {', '.join(sorted(missing_root))}")
        allowed = ROOT_TABLES | set(EXPECTED_COLUMNS) | {LEGACY_LEDGER, API_LEDGER}
        unexpected = tables - allowed
        if unexpected:
            errors.append(f"unexpected tables: {', '.join(sorted(unexpected))}")

    for table, expected in EXPECTED_COLUMNS.items():
        if table not in tables:
            errors.append(f"missing table: {table}")
            continue
        columns = {column["name"]: column for column in inspector.get_columns(table)}
        missing = expected - set(columns)
        if missing:
            errors.append(f"{table} missing columns: {', '.join(sorted(missing))}")
        if formal:
            unexpected_columns = set(columns) - expected
            if unexpected_columns:
                errors.append(
                    f"{table} unexpected columns: {', '.join(sorted(unexpected_columns))}"
                )

    if "idempotency_records" in tables:
        columns = {item["name"]: item for item in inspector.get_columns("idempotency_records")}
        review_id = columns.get("review_id")
        length = getattr(review_id["type"], "length", None) if review_id else None
        if review_id is not None and length is not None and length < 64:
            errors.append(f"idempotency_records.review_id length is {length}, expected at least 64")
        unique_names = {
            item.get("name") for item in inspector.get_unique_constraints("idempotency_records")
        }
        if "uq_idempotency_scope" not in unique_names:
            errors.append("idempotency_records missing unique constraint: uq_idempotency_scope")

    if "plan_reviews" in tables:
        columns = {item["name"]: item for item in inspector.get_columns("plan_reviews")}
        plan_id = columns.get("plan_id")
        normalized = columns.get("normalized_request_json")
        if plan_id is not None and str(plan_id["type"]).upper() != "TEXT":
            errors.append(f"plan_reviews.plan_id type is {plan_id['type']}, expected TEXT")
        if normalized is not None and normalized.get("nullable"):
            errors.append("plan_reviews.normalized_request_json must be NOT NULL")

    for table, expected in EXPECTED_INDEXES.items():
        if table not in tables:
            continue
        actual = {item["name"] for item in inspector.get_indexes(table)}
        missing = expected - actual
        if missing:
            errors.append(f"{table} missing indexes: {', '.join(sorted(missing))}")
        if formal:
            unexpected_indexes = actual - expected
            if unexpected_indexes:
                errors.append(
                    f"{table} unexpected indexes: {', '.join(sorted(unexpected_indexes))}"
                )

    row_counts = (
        {
            table: connection.execute(text(f'SELECT count(*) FROM "{table}"')).scalar_one()
            for table in sorted(ROOT_TABLES | set(EXPECTED_COLUMNS))
            if table in tables
        }
        if formal else {}
    )

    details: dict[str, object] = {
        "database": database,
        "api_head": api_head(),
        "legacy_ledger": ledger_values(connection, LEGACY_LEDGER, tables),
        "api_ledger": ledger_values(connection, API_LEDGER, tables),
        "api_tables": sorted(table for table in EXPECTED_COLUMNS if table in tables),
        "row_counts": row_counts,
    }
    if formal and details["legacy_ledger"] != [ROOT_HEAD]:
        errors.append(
            f"root ledger must be exactly {[ROOT_HEAD]}, found {details['legacy_ledger']}"
        )
    if formal and details["api_ledger"] not in (None, [], [details["api_head"]]):
        errors.append(f"API ledger is not absent, empty, or at head: {details['api_ledger']}")
    return errors, details


def print_report(errors: list[str], details: dict[str, object]) -> None:
    print(f"database: {details['database']}")
    print(f"expected API head: {details['api_head']}")
    print(f"legacy alembic_version: {details['legacy_ledger']}")
    print(f"API api_alembic_version: {details['api_ledger']}")
    print(f"existing API tables: {', '.join(details['api_tables'])}")
    if details.get("row_counts"):
        print(
            "preservation row counts: "
            + ", ".join(
                f"{table}={count}"
                for table, count in dict(details["row_counts"]).items()
            )
        )
    if errors:
        print("schema audit: FAIL")
        for error in errors:
            print(f"- {error}")
    else:
        print("schema audit: PASS (matches API migration head)")


def validate_target_url(raw_url: str, *, variable: str, formal: bool) -> None:
    parsed = make_url(raw_url)
    if parsed.get_backend_name() != "postgresql" or parsed.get_driver_name() != "psycopg":
        raise ValueError(f"{variable} must use postgresql+psycopg")
    if formal:
        if parsed.database != FORMAL_DATABASE:
            raise ValueError(
                f"{variable} must name the formal database {FORMAL_DATABASE}"
            )
    elif not parsed.database or not parsed.database.endswith("_test"):
        raise ValueError(f"{variable} database name must end with _test")


def validate_apply_environment(*, formal: bool) -> None:
    if os.getenv("ALLOW_API_LEDGER_RECONCILE") != "1":
        raise RuntimeError("set ALLOW_API_LEDGER_RECONCILE=1 before using --apply")
    if not formal:
        return
    if os.getenv("ALLOW_FORMAL_API_LEDGER_RECONCILE") != FORMAL_DATABASE:
        raise RuntimeError(
            f"formal --apply requires ALLOW_FORMAL_API_LEDGER_RECONCILE={FORMAL_DATABASE}"
        )
    backup_reference = os.getenv("MTA_DATA_BACKUP_REFERENCE", "").strip()
    if os.getenv("MTA_DATA_BACKUP_CONFIRMED") != "1" or not backup_reference:
        raise RuntimeError(
            "formal --apply requires MTA_DATA_BACKUP_CONFIRMED=1 and "
            "a non-empty MTA_DATA_BACKUP_REFERENCE"
        )


def adopt_verified_ledger(
    connection: Connection, *, formal: bool, expected_head: str
) -> bool:
    """Adopt the API head while locked; return whether a row was inserted."""
    connection.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": LOCK_ID})
    locked_errors, before = audit(connection, formal=formal)
    if locked_errors:
        raise RuntimeError("schema changed after audit: " + "; ".join(locked_errors))
    current = before["api_ledger"]
    if current == [expected_head]:
        return False
    if current not in (None, []):
        raise RuntimeError(f"refusing to replace non-empty API ledger: {current}")
    before_counts = dict(before["row_counts"]) if formal else {}
    connection.execute(
        text(
            "CREATE TABLE IF NOT EXISTS api_alembic_version "
            "(version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
        )
    )
    connection.execute(
        text("INSERT INTO api_alembic_version (version_num) VALUES (:revision)"),
        {"revision": expected_head},
    )
    if formal:
        after_errors, after = audit(connection, formal=True)
        if after_errors:
            raise RuntimeError("post-adoption audit failed: " + "; ".join(after_errors))
        after_counts = dict(after["row_counts"])
        if after_counts != before_counts:
            raise RuntimeError(
                "formal business-table row counts changed during ledger adoption: "
                f"before={before_counts}, after={after_counts}"
            )
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit or reconcile the Ontology Review API Alembic ledger."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="record the verified API head in api_alembic_version",
    )
    parser.add_argument(
        "--formal", action="store_true",
        help="audit the formal mta_data database (reads FORMAL_POSTGRES_URL)",
    )
    args = parser.parse_args()

    url_variable = "FORMAL_POSTGRES_URL" if args.formal else "TEST_POSTGRES_URL"
    raw_url = os.getenv(url_variable)
    if not raw_url:
        print(f"{url_variable} is not set", file=sys.stderr)
        return 2
    try:
        validate_target_url(raw_url, variable=url_variable, formal=args.formal)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    engine = create_engine(raw_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            errors, details = audit(connection, formal=args.formal)
            print_report(errors, details)
        if errors:
            print("No changes made.")
            return 1
        if not args.apply:
            print("Read-only audit complete. No changes made.")
            return 0
        try:
            validate_apply_environment(formal=args.formal)
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 2

        expected_head = str(details["api_head"])
        with engine.begin() as connection:
            changed = adopt_verified_ledger(
                connection, formal=args.formal, expected_head=expected_head
            )
        if not changed:
            print(f"API ledger already records {expected_head}; no change needed.")
            return 0
        print(f"APPLIED: api_alembic_version now records {expected_head}")
        print("Legacy alembic_version and all business data were left unchanged.")
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
