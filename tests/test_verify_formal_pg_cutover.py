from __future__ import annotations

import pytest

from scripts import verify_formal_pg_cutover as verify


class RecordingConnection:
    def __init__(self):
        self.calls = []

    def execute(self, statement, parameters):
        self.calls.append((str(statement), parameters))


def test_formal_smoke_requires_dedicated_confirmation(monkeypatch):
    monkeypatch.delenv("ALLOW_FORMAL_POSTGRES_SMOKE", raising=False)
    monkeypatch.setenv("MTA_DATA_BACKUP_REFERENCE", "snapshot-123")
    with pytest.raises(RuntimeError, match="ALLOW_FORMAL_POSTGRES_SMOKE=mta_data"):
        verify.validate_smoke_environment()


def test_formal_smoke_requires_backup_reference(monkeypatch):
    monkeypatch.setenv("ALLOW_FORMAL_POSTGRES_SMOKE", "mta_data")
    monkeypatch.delenv("MTA_DATA_BACKUP_REFERENCE", raising=False)
    with pytest.raises(RuntimeError, match="MTA_DATA_BACKUP_REFERENCE"):
        verify.validate_smoke_environment()


def test_formal_smoke_accepts_both_guards(monkeypatch):
    monkeypatch.setenv("ALLOW_FORMAL_POSTGRES_SMOKE", "mta_data")
    monkeypatch.setenv("MTA_DATA_BACKUP_REFERENCE", "snapshot-123")
    assert verify.validate_smoke_environment() == "snapshot-123"


def test_current_frozen_release_verifies_with_pending_r5():
    result = verify.verify_release()
    assert result["manifest"]["ontology_version"] == "2.1-campaign-pending"
    assert len(result["r5_sha256"]) == 64


def test_post_cutover_database_requires_complete_api_schema(monkeypatch):
    monkeypatch.setattr(
        verify.reconcile,
        "audit",
        lambda connection, **kwargs: (
            [],
            {"api_state": "absent", "api_ledger": None, "api_head": "head"},
        ),
    )
    with pytest.raises(RuntimeError, match="must be complete"):
        verify.verify_database(object())


def test_post_cutover_database_requires_exact_api_head(monkeypatch):
    monkeypatch.setattr(
        verify.reconcile,
        "audit",
        lambda connection, **kwargs: (
            [],
            {"api_state": "complete", "api_ledger": [], "api_head": "head"},
        ),
    )
    with pytest.raises(RuntimeError, match="exactly the API head"):
        verify.verify_database(object())


def test_post_cutover_database_accepts_exact_state(monkeypatch):
    details = {
        "api_state": "complete", "api_ledger": ["head"], "api_head": "head"
    }
    monkeypatch.setattr(
        verify.reconcile,
        "audit",
        lambda connection, **kwargs: ([], details),
    )
    assert verify.verify_database(object()) is details


def test_smoke_cleanup_deletes_only_unique_scoped_rows():
    connection = RecordingConnection()
    verify._cleanup_smoke(
        connection,
        client_id="demo_client_001",
        plan_id="plan_formal_cutover_unique",
        principal_id="formal-smoke-unique",
        idempotency_key="formal-cutover-unique",
    )

    assert len(connection.calls) == 6
    assert all(" WHERE " in sql for sql, _ in connection.calls)
    assert connection.calls[0][1] == {
        "principal": "formal-smoke-unique", "key": "formal-cutover-unique"
    }
    assert connection.calls[1][1] == {"plan_id": "plan_formal_cutover_unique"}
    assert all(
        params == {"client": "demo_client_001", "plan": "plan_formal_cutover_unique"}
        for _, params in connection.calls[2:]
    )
