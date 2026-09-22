import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from stock_research_llm_orchestrator.requests.production import (
    ProductionLogicalRequest,
    ProductionPhysicalAttempt,
    RuntimeLeasePolicy,
)
from stock_research_llm_orchestrator.requests.storage import (
    DATABASE_FILENAME,
    ProductionRequestRepository,
    RuntimeStorageError,
    initialize_runtime_storage,
)


NOW = datetime(2026, 9, 22, tzinfo=UTC)
POLICY = RuntimeLeasePolicy()


def _repository(tmp_path: Path) -> tuple[ProductionRequestRepository, Path]:
    root = tmp_path / ".runtime"
    root.mkdir(mode=0o700)
    return initialize_runtime_storage(root), root / DATABASE_FILENAME


def _add_request_and_attempt(repository: ProductionRequestRepository) -> None:
    repository.add_logical_request(
        ProductionLogicalRequest(
            logical_request_id="logical-1",
            task_id="task-1",
            source_id="fixture",
            operation="history",
            request_fingerprint="a" * 64,
            source_approval_version=1,
            source_profile_version=1,
            credential_scope_alias=None,
            egress_scope="default",
            created_at=NOW.isoformat(),
        )
    )
    repository.add_physical_attempt(
        ProductionPhysicalAttempt(
            physical_attempt_id="attempt-1",
            logical_request_id="logical-1",
            sequence_number=1,
            lease_generation=1,
            created_at=NOW.isoformat(),
        )
    )


def test_recovery_transfers_unsent_reservation_to_new_generation(tmp_path: Path) -> None:
    """Keep an unsent reservation eligible without creating another attempt."""
    repository, _ = _repository(tmp_path)
    repository.acquire_lease("owner-a", NOW, POLICY)
    _add_request_and_attempt(repository)

    recovered = repository.recover_expired_lease("owner-b", NOW + timedelta(seconds=30), POLICY)

    assert recovered.generation == 2
    assert repository.physical_attempt_state("attempt-1") == ("reserved", 2)
    with pytest.raises(RuntimeStorageError, match="physical_attempt_persistence_failed"):
        repository.add_physical_attempt(
            ProductionPhysicalAttempt(
                physical_attempt_id="attempt-2",
                logical_request_id="logical-1",
                sequence_number=1,
                lease_generation=2,
                created_at=(NOW + timedelta(seconds=30)).isoformat(),
            )
        )


def test_recovery_marks_started_attempt_unknown_without_resend(tmp_path: Path) -> None:
    """Conservatively terminate work that may already have reached a provider."""
    repository, database = _repository(tmp_path)
    lease = repository.acquire_lease("owner-a", NOW, POLICY)
    _add_request_and_attempt(repository)
    repository.mark_physical_attempt_started("attempt-1", lease, NOW + timedelta(seconds=1))

    recovered = repository.recover_expired_lease("owner-b", NOW + timedelta(seconds=30), POLICY)

    assert recovered.generation == 2
    assert repository.physical_attempt_state("attempt-1") == ("unknown", 1)
    assert repository.logical_request_state("logical-1") == "failed"
    with sqlite3.connect(database) as connection:
        result = connection.execute(
            "SELECT outcome, error_code FROM logical_results WHERE logical_request_id = 'logical-1'"
        ).fetchone()
    assert result == ("unknown", "owner_interrupted")


def test_recovery_rejects_active_lease_and_clock_rollback(tmp_path: Path) -> None:
    """Require expiry and a non-decreasing wall clock before takeover."""
    repository, _ = _repository(tmp_path)
    repository.acquire_lease("owner-a", NOW, POLICY)

    with pytest.raises(RuntimeStorageError, match="runtime_lease_held"):
        repository.recover_expired_lease("owner-b", NOW + timedelta(seconds=29), POLICY)
    with pytest.raises(RuntimeStorageError, match="runtime_clock_rollback"):
        repository.recover_expired_lease("owner-b", NOW - timedelta(seconds=1), POLICY)


def test_recovery_fails_closed_on_terminal_attempt_audit_gap(tmp_path: Path) -> None:
    """Reject a partial or externally corrupted terminal transition."""
    repository, database = _repository(tmp_path)
    repository.acquire_lease("owner-a", NOW, POLICY)
    _add_request_and_attempt(repository)
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE physical_attempts SET state = 'succeeded' WHERE physical_attempt_id = 'attempt-1'")

    with pytest.raises(RuntimeStorageError, match="runtime_recovery_audit_gap"):
        repository.recover_expired_lease("owner-b", NOW + timedelta(seconds=30), POLICY)

    with pytest.raises(RuntimeStorageError, match="lease_reconciliation_required"):
        repository.acquire_lease("owner-b", NOW + timedelta(seconds=31), POLICY)


def test_recovery_fails_closed_on_future_attempt_generation(tmp_path: Path) -> None:
    """Reject fencing generations that cannot belong to the expired owner."""
    repository, database = _repository(tmp_path)
    repository.acquire_lease("owner-a", NOW, POLICY)
    _add_request_and_attempt(repository)
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE physical_attempts SET lease_generation = 99")

    with pytest.raises(RuntimeStorageError, match="runtime_recovery_clock_or_generation_anomaly"):
        repository.recover_expired_lease("owner-b", NOW + timedelta(seconds=30), POLICY)
