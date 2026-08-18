from __future__ import annotations

import pytest

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


class FakeConnection:
    def __init__(self):
        self.statements: list[str] = []

    def execute(self, statement, parameters=None):
        self.statements.append(str(statement))
        return object()


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
