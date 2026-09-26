from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from stock_research_llm_orchestrator.requests.production import (
    LogicalResultOutcome,
    ProductionLogicalRequest,
    ProductionLogicalResult,
    RuntimeLeasePolicy,
)
from stock_research_llm_orchestrator.requests.storage import (
    ProductionRequestRepository,
    RuntimeStorageError,
    initialize_runtime_storage,
)


NOW = datetime(2026, 9, 21, tzinfo=UTC)
POLICY = RuntimeLeasePolicy()


def _repository(tmp_path: Path) -> ProductionRequestRepository:
    root = tmp_path / ".runtime"
    root.mkdir(mode=0o700)
    return initialize_runtime_storage(root)


def test_lease_policy_rejects_slow_heartbeat() -> None:
    """Require at least three heartbeat opportunities per lease."""
    with pytest.raises(ValidationError, match="one third"):
        RuntimeLeasePolicy(lease_duration_seconds=30, heartbeat_interval_seconds=11)


def test_acquire_rejects_invalid_owner_before_persistence(tmp_path: Path) -> None:
    """Validate the owner token before writing the singleton lease row."""
    repository = _repository(tmp_path)

    with pytest.raises(ValidationError):
        repository.acquire_lease("owner token with spaces", NOW, POLICY)

    assert repository.acquire_lease("owner-a", NOW, POLICY).generation == 1


def test_acquire_heartbeat_validate_and_release(tmp_path: Path) -> None:
    """Exercise the normal lease lifecycle with an increasing generation."""
    repository = _repository(tmp_path)
    first = repository.acquire_lease("owner-a", NOW, POLICY)
    renewed = repository.heartbeat_lease(first, NOW + timedelta(seconds=10), POLICY)
    repository.validate_fence(renewed, NOW + timedelta(seconds=20))
    repository.release_lease(renewed, NOW + timedelta(seconds=20))

    second = repository.acquire_lease("owner-b", NOW + timedelta(seconds=21), POLICY)

    assert first.generation == 1
    assert second.generation == 2


def test_active_lease_rejects_second_owner(tmp_path: Path) -> None:
    """Prevent a second owner from acquiring an active runtime."""
    repository = _repository(tmp_path)
    repository.acquire_lease("owner-a", NOW, POLICY)

    with pytest.raises(RuntimeStorageError, match="runtime_lease_held"):
        repository.acquire_lease("owner-b", NOW + timedelta(seconds=1), POLICY)


def test_expired_lease_requires_reconciliation(tmp_path: Path) -> None:
    """Do not silently take over an expired owner before restart recovery."""
    repository = _repository(tmp_path)
    repository.acquire_lease("owner-a", NOW, POLICY)

    with pytest.raises(RuntimeStorageError, match="lease_reconciliation_required"):
        repository.acquire_lease("owner-b", NOW + timedelta(seconds=30), POLICY)


def test_stale_owner_cannot_heartbeat_release_or_validate(tmp_path: Path) -> None:
    """Reject all mutations from a released fencing generation."""
    repository = _repository(tmp_path)
    stale = repository.acquire_lease("owner-a", NOW, POLICY)
    repository.release_lease(stale, NOW + timedelta(seconds=1))
    repository.acquire_lease("owner-b", NOW + timedelta(seconds=2), POLICY)

    with pytest.raises(RuntimeStorageError, match="stale_runtime_lease"):
        repository.heartbeat_lease(stale, NOW + timedelta(seconds=3), POLICY)
    with pytest.raises(RuntimeStorageError, match="stale_runtime_lease"):
        repository.release_lease(stale, NOW + timedelta(seconds=3))
    with pytest.raises(RuntimeStorageError, match="stale_runtime_lease"):
        repository.validate_fence(stale, NOW + timedelta(seconds=3))


def test_clock_rollback_and_expiry_fail_closed(tmp_path: Path) -> None:
    """Reject backward clocks and operations at the expiry boundary."""
    repository = _repository(tmp_path)
    lease = repository.acquire_lease("owner-a", NOW, POLICY)

    with pytest.raises(RuntimeStorageError, match="runtime_clock_rollback"):
        repository.heartbeat_lease(lease, NOW - timedelta(seconds=1), POLICY)
    with pytest.raises(RuntimeStorageError, match="runtime_lease_expired"):
        repository.validate_fence(lease, NOW + timedelta(seconds=30))


def test_expired_owner_cannot_commit_delayed_result(tmp_path: Path) -> None:
    """Fence result persistence so an expired owner cannot commit late."""
    repository = _repository(tmp_path)
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
    lease = repository.acquire_lease("owner-a", NOW, POLICY)
    result = ProductionLogicalResult(
        logical_request_id="logical-1",
        outcome=LogicalResultOutcome.SUCCEEDED,
        completed_at=(NOW + timedelta(seconds=31)).isoformat(),
        error_code=None,
    )

    with pytest.raises(RuntimeStorageError, match="runtime_lease_expired"):
        repository.complete_logical_request(result, lease, NOW + timedelta(seconds=31))

    assert repository.logical_request_state("logical-1") == "queued"


def test_two_owners_cannot_win_concurrent_acquisition(tmp_path: Path) -> None:
    """Serialize competing owners so exactly one acquires the active lease."""
    repository = _repository(tmp_path)

    def acquire(owner: str) -> str:
        try:
            repository.acquire_lease(owner, NOW, POLICY)
        except RuntimeStorageError as error:
            return str(error)
        return "acquired"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(acquire, ("owner-a", "owner-b")))

    assert sorted(outcomes) == ["acquired", "runtime_lease_held"]
