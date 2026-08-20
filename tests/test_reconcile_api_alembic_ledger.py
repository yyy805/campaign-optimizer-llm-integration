from __future__ import annotations

import pytest
from sqlalchemy import CheckConstraint, Column, ForeignKey, Integer, MetaData, String, Table, create_engine

import scripts.reconcile_api_alembic_ledger as reconcile
from scripts.reconcile_api_alembic_ledger import FORMAL_DATABASE, ROOT_HEAD, ROOT_TABLES


def test_formal_contract_is_explicit_and_cannot_alias_test_database():
    assert FORMAL_DATABASE == "mta_data"
    assert not FORMAL_DATABASE.endswith("_test")
    assert ROOT_HEAD == "7b8f3d1a2c4e"
    assert len(ROOT_TABLES) == 13


def test_root_and_api_ledgers_remain_independent():
    from scripts.reconcile_api_alembic_ledger import API_LEDGER, LEGACY_LEDGER

    assert API_LEDGER == "api_alembic_version"
    assert LEGACY_LEDGER == "alembic_version"
    assert API_LEDGER != LEGACY_LEDGER


def test_formal_url_errors_name_the_active_variable():
    with pytest.raises(ValueError, match="FORMAL_POSTGRES_URL must use"):
        reconcile.validate_target_url(
            "sqlite:///mta_data", variable="FORMAL_POSTGRES_URL", formal=True
        )
    with pytest.raises(ValueError, match="FORMAL_POSTGRES_URL.*mta_data"):
        reconcile.validate_target_url(
            "postgresql+psycopg://host/mta_data_test",
            variable="FORMAL_POSTGRES_URL", formal=True,
        )
    with pytest.raises(ValueError, match="approved formal host"):
        reconcile.validate_target_url(
            "postgresql+psycopg://user:secret@wrong.example/mta_data",
            variable="FORMAL_POSTGRES_URL", formal=True,
        )
    reconcile.validate_target_url(
        f"postgresql+psycopg://user:secret@{reconcile.FORMAL_HOST}:5432/mta_data",
        variable="FORMAL_POSTGRES_URL", formal=True,
    )


def test_formal_apply_needs_dedicated_token_and_backup(monkeypatch):
    monkeypatch.setenv("ALLOW_API_LEDGER_RECONCILE", "1")
    monkeypatch.setenv("MTA_DATA_BACKUP_CONFIRMED", "1")
    monkeypatch.setenv("MTA_DATA_BACKUP_REFERENCE", "snapshot-123")
    with pytest.raises(RuntimeError, match="ALLOW_FORMAL_API_LEDGER_RECONCILE=mta_data"):
        reconcile.validate_apply_environment(formal=True)
    monkeypatch.setenv("ALLOW_FORMAL_API_LEDGER_RECONCILE", "mta_data")
    reconcile.validate_apply_environment(formal=True)
    monkeypatch.delenv("MTA_DATA_BACKUP_REFERENCE")
    with pytest.raises(RuntimeError, match="BACKUP_REFERENCE"):
        reconcile.validate_apply_environment(formal=True)
    monkeypatch.setenv("MTA_DATA_BACKUP_REFERENCE", "<snapshot-reference>")
    with pytest.raises(RuntimeError, match="placeholder"):
        reconcile.validate_apply_environment(formal=True)


class FakeConnection:
    def __init__(self):
        self.statements: list[str] = []

    def execute(self, statement, parameters=None):
        self.statements.append(str(statement))
        return object()


def test_check_dump_does_not_invoke_strict_parser(monkeypatch, capsys):
    class FakeInspector:
        def get_table_names(self):
            return ["sample"]

        def get_check_constraints(self, table_name):
            return [{"name": "ck_sample", "sqltext": "strange_pg_syntax(value)"}]

    monkeypatch.setattr(reconcile, "inspect", lambda connection: FakeInspector())
    reconcile.dump_check_constraints(object())
    assert capsys.readouterr().out.strip() == (
        "sample | ck_sample | strange_pg_syntax(value)"
    )


def test_formal_adoption_locks_then_reaudits_unchanged_counts(monkeypatch):
    connection = FakeConnection()
    reports = iter([
        ([], {"api_ledger": None, "row_counts": {"reviews": 7}}),
        ([], {"api_ledger": ["head"], "row_counts": {"reviews": 7}}),
    ])
    monkeypatch.setattr(
        reconcile, "audit", lambda connection, formal, **kwargs: next(reports)
    )
    assert reconcile.adopt_verified_ledger(
        connection, formal=True, expected_head="head"
    ) is True
    assert "pg_advisory_xact_lock" in connection.statements[0]
    assert "CREATE TABLE" in connection.statements[1]
    assert "INSERT INTO api_alembic_version" in connection.statements[2]


def test_formal_adoption_raises_on_business_count_change(monkeypatch):
    connection = FakeConnection()
    reports = iter([
        ([], {"api_ledger": [], "row_counts": {"reviews": 7}}),
        ([], {"api_ledger": ["head"], "row_counts": {"reviews": 8}}),
    ])
    monkeypatch.setattr(
        reconcile, "audit", lambda connection, formal, **kwargs: next(reports)
    )
    with pytest.raises(RuntimeError, match="row counts changed"):
        reconcile.adopt_verified_ledger(connection, formal=True, expected_head="head")


def test_formal_cutover_locks_repairs_known_ancestor_and_runs_api_migrations(monkeypatch):
    connection = FakeConnection()
    before = {
        "api_state": "absent",
        "api_ledger": None,
        "legacy_ledger": sorted([reconcile.ROOT_HEAD, reconcile.ROOT_ANCESTOR]),
        "all_tables": ["clients", "algorithm_table", "alembic_version"],
        "row_counts": {"clients": 2, "algorithm_table": 9},
    }
    after = {
        "api_state": "complete",
        "api_ledger": ["head"],
        "legacy_ledger": [reconcile.ROOT_HEAD],
        "all_tables": [
            "clients", "algorithm_table", "alembic_version",
            "reviews", "idempotency_records", "plan_reviews", "api_alembic_version",
        ],
        "row_counts": {
            "clients": 2, "algorithm_table": 9,
            "reviews": 0, "idempotency_records": 0, "plan_reviews": 0,
        },
    }
    reports = iter([( [], before), ([], after)])
    monkeypatch.setattr(
        reconcile, "audit", lambda connection, formal, **kwargs: next(reports)
    )
    migrated = []
    monkeypatch.setattr(reconcile, "api_config", lambda connection: "config")
    monkeypatch.setattr(
        reconcile.command, "upgrade", lambda config, head: migrated.append((config, head))
    )

    reconcile.complete_formal_cutover(connection, expected_head="head")

    assert "pg_advisory_xact_lock" in connection.statements[0]
    assert "DELETE FROM alembic_version" in connection.statements[1]
    assert migrated == [("config", "head")]


def test_formal_cutover_refuses_partial_api_schema_before_any_write(monkeypatch):
    connection = FakeConnection()
    monkeypatch.setattr(
        reconcile,
        "audit",
        lambda connection, formal, **kwargs: (
            ["partial API schema: found reviews"],
            {"api_state": "partial", "api_ledger": None},
        ),
    )
    with pytest.raises(RuntimeError, match="locked pre-migration audit failed"):
        reconcile.complete_formal_cutover(connection, expected_head="head")
    assert len(connection.statements) == 1
    assert "pg_advisory_xact_lock" in connection.statements[0]


def test_formal_cutover_refuses_preexisting_count_change(monkeypatch):
    connection = FakeConnection()
    before = {
        "api_state": "absent", "api_ledger": None,
        "legacy_ledger": [reconcile.ROOT_HEAD],
        "all_tables": ["clients", "alembic_version"],
        "row_counts": {"clients": 2},
    }
    after = {
        "api_state": "complete", "api_ledger": ["head"],
        "legacy_ledger": [reconcile.ROOT_HEAD],
        "all_tables": [
            "clients", "alembic_version", "reviews", "idempotency_records",
            "plan_reviews", "api_alembic_version",
        ],
        "row_counts": {
            "clients": 3, "reviews": 0, "idempotency_records": 0, "plan_reviews": 0,
        },
    }
    reports = iter([([], before), ([], after)])
    monkeypatch.setattr(
        reconcile, "audit", lambda connection, formal, **kwargs: next(reports)
    )
    monkeypatch.setattr(reconcile, "api_config", lambda connection: "config")
    monkeypatch.setattr(reconcile.command, "upgrade", lambda config, head: None)
    with pytest.raises(RuntimeError, match="preexisting-table row counts changed"):
        reconcile.complete_formal_cutover(connection, expected_head="head")


def test_schema_fingerprint_checks_constraints_types_and_nullability():
    metadata = MetaData()
    Table("parent", metadata, Column("id", Integer, primary_key=True))
    child = Table(
        "child", metadata,
        Column("id", Integer, primary_key=True),
        Column("parent_id", Integer, ForeignKey("parent.id"), nullable=False),
        Column("code", String(20), nullable=False, unique=True),
        CheckConstraint("length(code) > 0", name="ck_child_code"),
    )
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    with engine.connect() as connection:
        assert reconcile.schema_fingerprint_errors(connection, "child", child) == []

        drifted = MetaData()
        Table("parent", drifted, Column("id", Integer, primary_key=True))
        drifted_child = Table(
            "child", drifted,
            Column("id", Integer, primary_key=True),
            Column("parent_id", Integer, ForeignKey("parent.id"), nullable=True),
            Column("code", String(30), nullable=False, unique=True),
            CheckConstraint("length(code) > 1", name="ck_child_code"),
        )
        errors = reconcile.schema_fingerprint_errors(connection, "child", drifted_child)
    assert any("type expected" in error for error in errors)
    assert any("nullable expected" in error for error in errors)
    assert any("checks expected" in error for error in errors)


def test_check_signature_preserves_operator_and_boolean_semantics():
    columns = {"runtime_confidence", "revision"}
    approved = reconcile._check_signature(
        "runtime_confidence >= 0 AND runtime_confidence <= 1", columns
    )
    reversed_operator = reconcile._check_signature(
        "runtime_confidence <= 0 AND runtime_confidence <= 1", columns
    )
    reversed_boolean = reconcile._check_signature(
        "runtime_confidence >= 0 OR runtime_confidence <= 1", columns
    )
    assert approved != reversed_operator
    assert approved != reversed_boolean
    grouped_left = reconcile._check_signature(
        "revision = 1 AND (runtime_confidence = 2 OR runtime_confidence = 3)", columns
    )
    grouped_right = reconcile._check_signature(
        "(revision = 1 AND runtime_confidence = 2) OR runtime_confidence = 3", columns
    )
    assert grouped_left != grouped_right
    assert reconcile._check_signature("abs(revision) > 0", columns) != reconcile._check_signature(
        "sqrt(revision) > 0", columns
    )


def test_postgres_any_array_check_normalizes_to_in():
    columns = {"status"}
    expected = reconcile._check_signature(
        "status IN ('ACTIVE', 'SUSPENDED', 'RETIRED')", columns
    )
    reflected = reconcile._check_signature(
        "status = ANY ((ARRAY['ACTIVE'::character varying, "
        "'SUSPENDED'::character varying, 'RETIRED'::character varying])::text[])",
        columns,
    )
    assert reflected == expected


def test_postgres_any_array_does_not_consume_surrounding_parenthesis():
    columns = {"action"}
    expected = reconcile._check_signature(
        "action IS NULL OR action IN ('increase_budget', 'decrease_budget', 'keep_budget')",
        columns,
    )
    reflected = reconcile._check_signature(
        "action IS NULL OR (action::text = ANY (ARRAY["
        "'increase_budget'::character varying, 'decrease_budget'::character varying, "
        "'keep_budget'::character varying]::text[]))",
        columns,
    )
    assert reflected == expected


def test_postgres_double_precision_check_normalizes_to_metadata_expression():
    columns = {"runtime_confidence"}
    expected = reconcile._check_signature(
        "runtime_confidence >= 0 AND runtime_confidence <= 1", columns
    )
    reflected = reconcile._check_signature(
        "(runtime_confidence >= (0)::double precision) AND "
        "(runtime_confidence <= (1)::double precision)",
        columns,
    )
    assert reflected == expected


def test_unique_constraint_backing_indexes_are_not_double_counted():
    indexes = [
        {"name": "ix_real", "column_names": ["value"], "unique": False},
        {
            "name": "uq_value", "column_names": ["value"], "unique": True,
            "duplicates_constraint": "uq_value",
        },
    ]
    assert reconcile._independent_indexes(indexes) == [indexes[0]]


def test_metadata_baseline_is_pinned_to_approved_heads():
    assert reconcile.metadata_baseline_sha256() == reconcile.EXPECTED_METADATA_BASELINE_SHA256
    payload = reconcile._expected_schema(reconcile.API_METADATA.tables["idempotency_records"])
    assert payload["columns"]["id"]["autoincrement"] in (True, "auto")


def test_foreign_key_fingerprint_includes_actions_and_deferrability():
    metadata = MetaData()
    Table("parent", metadata, Column("id", Integer, primary_key=True))
    child = Table(
        "child", metadata,
        Column("id", Integer, primary_key=True),
        Column(
            "parent_id", Integer,
            ForeignKey("parent.id", ondelete="CASCADE", onupdate="RESTRICT", deferrable=True),
        ),
    )
    fingerprints = reconcile._expected_schema(child)["foreign_keys"]
    assert any("CASCADE" in item and "RESTRICT" in item and True in item for item in fingerprints)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("'legacy'::character varying", "legacy"),
        ("'{}'::text", "{}"),
        ("'0'::integer", "0"),
    ],
)
def test_postgres_migration_defaults_are_normalized(raw, expected):
    assert reconcile._default_signature(raw) == expected
