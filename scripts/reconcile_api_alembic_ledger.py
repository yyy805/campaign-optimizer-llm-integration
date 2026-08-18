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

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Connection, make_url

from campaign_optimizer.ontology.db import Base as RootBase


REPO_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = REPO_ROOT / "ontology review api"
API_LEDGER = "api_alembic_version"
LEGACY_LEDGER = "alembic_version"
LOCK_ID = 6_038_024_217_952_623_953
FORMAL_DATABASE = "mta_data"
ROOT_HEAD = "7b8f3d1a2c4e"
ROOT_ANCESTOR = "da19a197a9f7"
ROOT_TABLES = {
    "concepts", "rules", "clients", "diagnoses", "execution_log",
    "model_artifacts", "plan_snapshots", "plan_items", "ontology_reviews",
    "ontology_review_items", "feedback_events", "rule_confidence_states",
    "plan_decision_events",
}
ROOT_HEAD_COLUMNS = {
    "parent_review_id", "revision", "rule_version", "engine_version",
    "schema_version", "source_commit", "package_checksum",
    "confidence_state_version",
}
ROOT_EXPECTED_COLUMNS = {
    table_name: {column.name for column in RootBase.metadata.tables[table_name].columns}
    for table_name in ROOT_TABLES
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


def audit(
    connection: Connection, *, formal: bool = False, allow_repairable: bool = False
) -> tuple[list[str], dict[str, object]]:
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
        for table_name, expected_columns in ROOT_EXPECTED_COLUMNS.items():
            if table_name not in tables:
                continue
            actual_columns = {
                column["name"] for column in inspector.get_columns(table_name)
            }
            missing_columns = expected_columns - actual_columns
            unexpected_columns = actual_columns - expected_columns
            if missing_columns:
                errors.append(
                    f"{table_name} missing root columns: "
                    + ", ".join(sorted(missing_columns))
                )
            if unexpected_columns:
                errors.append(
                    f"{table_name} unexpected root columns: "
                    + ", ".join(sorted(unexpected_columns))
                )
        if "ontology_reviews" in tables:
            root_columns = {
                column["name"] for column in inspector.get_columns("ontology_reviews")
            }
            missing_head_columns = ROOT_HEAD_COLUMNS - root_columns
            if missing_head_columns:
                errors.append(
                    "ontology_reviews missing root-head columns: "
                    + ", ".join(sorted(missing_head_columns))
                )

    present_api_tables = set(EXPECTED_COLUMNS) & tables
    api_state = (
        "absent" if not present_api_tables else
        "complete" if present_api_tables == set(EXPECTED_COLUMNS) else
        "partial"
    )
    if api_state == "partial":
        errors.append("partial API schema: found " + ", ".join(sorted(present_api_tables)))

    for table, expected in EXPECTED_COLUMNS.items():
        if table not in tables:
            if not (formal and allow_repairable and api_state == "absent"):
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

    protected_tables = tables - {LEGACY_LEDGER, API_LEDGER}
    row_counts = (
        {
            table: connection.execute(text(f'SELECT count(*) FROM "{table}"')).scalar_one()
            for table in sorted(protected_tables)
        }
        if formal else {}
    )

    details: dict[str, object] = {
        "database": database,
        "api_head": api_head(),
        "legacy_ledger": ledger_values(connection, LEGACY_LEDGER, tables),
        "api_ledger": ledger_values(connection, API_LEDGER, tables),
        "api_tables": sorted(table for table in EXPECTED_COLUMNS if table in tables),
        "api_state": api_state,
        "all_tables": sorted(tables),
        "row_counts": row_counts,
    }
    allowed_root_ledgers = [[ROOT_HEAD]]
    if allow_repairable:
        allowed_root_ledgers.append(sorted([ROOT_HEAD, ROOT_ANCESTOR]))
    if formal and details["legacy_ledger"] not in allowed_root_ledgers:
        errors.append(
            f"root ledger must be exactly {[ROOT_HEAD]}, found {details['legacy_ledger']}"
        )
    if formal and details["api_ledger"] not in (None, [], [details["api_head"]]):
        errors.append(f"API ledger is not absent, empty, or at head: {details['api_ledger']}")
    if (
        formal and allow_repairable and api_state == "absent"
        and details["api_ledger"] not in (None, [])
    ):
        errors.append("API tables are absent but API ledger is not absent or empty")
    return errors, details


def print_report(errors: list[str], details: dict[str, object]) -> None:
    print(f"database: {details['database']}")
    print(f"expected API head: {details['api_head']}")
    print(f"legacy alembic_version: {details['legacy_ledger']}")
    print(f"API api_alembic_version: {details['api_ledger']}")
    print(f"existing API tables: {', '.join(details['api_tables'])}")
    print(f"API schema state: {details['api_state']}")
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
        print("schema audit: PASS")


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


def api_config(connection: Connection) -> Config:
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "migrations"))
    config.attributes["connection"] = connection
    return config


def complete_formal_cutover(
    connection: Connection, *, expected_head: str
) -> dict[str, object]:
    """Repair the approved root ledger state and create the absent API schema."""
    connection.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": LOCK_ID})
    errors, before = audit(connection, formal=True, allow_repairable=True)
    if errors:
        raise RuntimeError("locked pre-migration audit failed: " + "; ".join(errors))
    if before["api_state"] != "absent" or before["api_ledger"] not in (None, []):
        raise RuntimeError(
            "formal migration requires all API tables and its ledger to be absent/empty"
        )

    legacy = before["legacy_ledger"]
    if legacy == sorted([ROOT_HEAD, ROOT_ANCESTOR]):
        connection.execute(
            text("DELETE FROM alembic_version WHERE version_num = :revision"),
            {"revision": ROOT_ANCESTOR},
        )
    elif legacy != [ROOT_HEAD]:
        raise RuntimeError(f"refusing unexpected root ledger: {legacy}")

    before_tables = set(before["all_tables"])
    before_counts = dict(before["row_counts"])
    command.upgrade(api_config(connection), expected_head)

    after_errors, after = audit(connection, formal=True)
    if after_errors:
        raise RuntimeError("post-migration audit failed: " + "; ".join(after_errors))
    allowed_new = set(EXPECTED_COLUMNS) | {API_LEDGER}
    actual_new = set(after["all_tables"]) - before_tables
    if actual_new - allowed_new:
        raise RuntimeError(f"unexpected tables created: {sorted(actual_new - allowed_new)}")
    after_counts = dict(after["row_counts"])
    preserved_after = {table: after_counts.get(table) for table in before_counts}
    if preserved_after != before_counts:
        raise RuntimeError(
            "preexisting-table row counts changed during formal migration: "
            f"before={before_counts}, after={preserved_after}"
        )
    return {"before": before, "after": after}


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
        if args.apply:
            try:
                validate_apply_environment(formal=args.formal)
            except RuntimeError as exc:
                print(str(exc), file=sys.stderr)
                return 2
            expected_head = api_head()
            if args.formal:
                with engine.begin() as connection:
                    result = complete_formal_cutover(
                        connection, expected_head=expected_head
                    )
                print_report([], dict(result["after"]))
                print(
                    "APPLIED: duplicate root ancestor removed when present; "
                    "API migrations reached head."
                )
                print("All preexisting table row counts were preserved.")
                return 0

        with engine.connect() as connection:
            errors, details = audit(
                connection, formal=args.formal, allow_repairable=args.formal
            )
            print_report(errors, details)
        if errors:
            print("No changes made.")
            return 1
        if not args.apply:
            print("Read-only audit complete. No changes made.")
            return 0
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
