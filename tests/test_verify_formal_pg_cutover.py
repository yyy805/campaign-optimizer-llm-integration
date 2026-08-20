from __future__ import annotations

import pytest

from scripts import verify_formal_pg_cutover as verify


class RecordingConnection:
    def __init__(self):
        self.calls = []

    def execute(self, statement, parameters):
        self.calls.append((str(statement), parameters))
        return RecordingResult()


class RecordingResult:
    def scalars(self):
        return iter(())


class ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value


def test_formal_smoke_requires_dedicated_confirmation(monkeypatch):
    monkeypatch.delenv("ALLOW_FORMAL_POSTGRES_SMOKE", raising=False)
    monkeypatch.setenv("MTA_DATA_BACKUP_REFERENCE", "snapshot-123")
    with pytest.raises(RuntimeError, match="ALLOW_FORMAL_POSTGRES_SMOKE=mta_data"):
        verify.validate_smoke_environment()


def test_formal_smoke_requires_backup_reference(monkeypatch):
    monkeypatch.setenv("ALLOW_FORMAL_POSTGRES_SMOKE", "mta_data")
    monkeypatch.setenv("ALLOW_FORMAL_POSTGRES_MAINTENANCE", "mta_data")
    monkeypatch.setenv("MTA_DATA_BACKUP_CONFIRMED", "1")
    monkeypatch.delenv("MTA_DATA_BACKUP_REFERENCE", raising=False)
    with pytest.raises(RuntimeError, match="MTA_DATA_BACKUP_REFERENCE"):
        verify.validate_smoke_environment()


def test_formal_smoke_requires_maintenance_and_backup_confirmation(monkeypatch):
    monkeypatch.setenv("ALLOW_FORMAL_POSTGRES_SMOKE", "mta_data")
    monkeypatch.setenv("MTA_DATA_BACKUP_REFERENCE", "snapshot-123")
    monkeypatch.setenv("MTA_DATA_BACKUP_CONFIRMED", "1")
    monkeypatch.delenv("ALLOW_FORMAL_POSTGRES_MAINTENANCE", raising=False)
    with pytest.raises(RuntimeError, match="MAINTENANCE=mta_data"):
        verify.validate_smoke_environment()
    monkeypatch.setenv("ALLOW_FORMAL_POSTGRES_MAINTENANCE", "mta_data")
    monkeypatch.delenv("MTA_DATA_BACKUP_CONFIRMED", raising=False)
    with pytest.raises(RuntimeError, match="BACKUP_CONFIRMED=1"):
        verify.validate_smoke_environment()


def test_formal_smoke_accepts_both_guards(monkeypatch):
    monkeypatch.setenv("ALLOW_FORMAL_POSTGRES_SMOKE", "mta_data")
    monkeypatch.setenv("ALLOW_FORMAL_POSTGRES_MAINTENANCE", "mta_data")
    monkeypatch.setenv("MTA_DATA_BACKUP_CONFIRMED", "1")
    monkeypatch.setenv("MTA_DATA_BACKUP_REFERENCE", "snapshot-123")
    assert verify.validate_smoke_environment() == "snapshot-123"


def test_formal_smoke_rejects_placeholder_backup(monkeypatch):
    monkeypatch.setenv("ALLOW_FORMAL_POSTGRES_SMOKE", "mta_data")
    monkeypatch.setenv("ALLOW_FORMAL_POSTGRES_MAINTENANCE", "mta_data")
    monkeypatch.setenv("MTA_DATA_BACKUP_CONFIRMED", "1")
    monkeypatch.setenv("MTA_DATA_BACKUP_REFERENCE", "<snapshot-reference>")
    with pytest.raises(RuntimeError, match="placeholder"):
        verify.validate_smoke_environment()


def test_current_frozen_release_verifies_with_pending_r5():
    result = verify.verify_release()
    assert result["manifest"]["ontology_version"] == "2.1-campaign-pending"
    assert result["manifest"]["source_commit"] == verify.EXPECTED_RELEASE["source_commit"]
    assert result["r5_sha256"] == verify.EXPECTED_R5_SHA256
    assert result["verified_manifest_count"] >= 2


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
    identity = verify.smoke_identity("unique-1234")
    verify._cleanup_smoke(connection, identity)

    assert len(connection.calls) == 8
    assert all(" WHERE " in sql for sql, _ in connection.calls)
    assert connection.calls[1][1] == {
        "principal": "formal-smoke-unique-1234", "key": "formal-cutover-unique-1234"
    }
    assert connection.calls[2][1] == {"plan_id": "plan_formal_cutover_unique-1234"}
    assert all(
        params == {"client": "cutover-client-001", "plan": "plan_formal_cutover_unique-1234"}
        for _, params in connection.calls[3:]
    )


def test_run_id_is_deterministic_and_strict():
    assert verify.smoke_identity("cutover-20260820-01") == verify.smoke_identity("cutover-20260820-01")
    with pytest.raises(ValueError, match="run ID"):
        verify.smoke_identity("BAD")


def test_primary_and_cleanup_failures_are_both_preserved():
    primary = RuntimeError("primary write failure")
    cleanup = RuntimeError("cleanup failure")
    with pytest.raises(ExceptionGroup) as caught:
        verify._raise_smoke_failures(primary, cleanup)
    assert caught.value.exceptions == (primary, cleanup)


def test_keyboard_interrupt_and_cleanup_failure_use_base_exception_group():
    primary = KeyboardInterrupt()
    cleanup = RuntimeError("cleanup failure")
    with pytest.raises(BaseExceptionGroup) as caught:
        verify._raise_smoke_failures(primary, cleanup)
    assert caught.value.exceptions == (primary, cleanup)


def test_verifier_lock_times_out_instead_of_waiting_forever(monkeypatch):
    class BusyConnection:
        def execute(self, statement, parameters):
            return ScalarResult(False)

        def commit(self):
            pass

    ticks = iter([0.0, 1.0])
    monkeypatch.setattr(verify.time, "monotonic", lambda: next(ticks))
    with pytest.raises(RuntimeError, match="timed out.*run_id=busy-run"):
        verify._acquire_verifier_lock(
            BusyConnection(), context="smoke run_id=busy-run", timeout_seconds=0.5
        )


def test_resource_errors_are_grouped_without_masking_primary():
    close_error = RuntimeError("close failed")
    dispose_error = RuntimeError("dispose failed")
    grouped = verify._group_errors("resources", [close_error, dispose_error])
    assert isinstance(grouped, ExceptionGroup)
    assert grouped.exceptions == (close_error, dispose_error)
