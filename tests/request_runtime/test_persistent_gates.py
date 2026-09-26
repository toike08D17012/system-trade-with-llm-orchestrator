from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.external_requests import GateScope
from stock_research_llm_orchestrator.requests.production import (
    GateKeys,
    GateLimit,
    HierarchicalGatePolicy,
    ProductionLogicalRequest,
    ProductionPhysicalAttempt,
    QueuePolicy,
    RuntimeLease,
    RuntimeLeasePolicy,
)
from stock_research_llm_orchestrator.requests.storage import (
    ProductionRequestRepository,
    RuntimeStorageError,
    initialize_runtime_storage,
)


NOW = datetime(2026, 9, 22, tzinfo=UTC)
KEYS = GateKeys(
    egress="default",
    provider="provider-a",
    origin="example-com",
    credential="anonymous",
    operation="history",
    task="task-1",
    role="researcher",
)


def _policy(
    *, concurrency: int = 1, interval: float = 0, requests: int = 10, window: float = 60
) -> HierarchicalGatePolicy:
    limit = GateLimit(
        max_concurrency=concurrency,
        min_interval_seconds=interval,
        requests_per_window=requests,
        window_seconds=window,
    )
    return HierarchicalGatePolicy(limits={scope: limit for scope in GateScope})


def _runtime(tmp_path: Path) -> tuple[ProductionRequestRepository, RuntimeLease]:
    root = tmp_path / ".runtime"
    root.mkdir(mode=0o700)
    repository = initialize_runtime_storage(root)
    return repository, repository.acquire_lease("owner-a", NOW, RuntimeLeasePolicy())


def _dequeue(
    repository: ProductionRequestRepository, lease: RuntimeLease, request_id: str, offset: int
) -> ProductionPhysicalAttempt:
    request = ProductionLogicalRequest(
        logical_request_id=request_id,
        task_id="task-1",
        source_id="fixture",
        operation="history",
        request_fingerprint=f"{offset + 1:064x}",
        source_approval_version=1,
        source_profile_version=1,
        credential_scope_alias=None,
        egress_scope="default",
        created_at=(NOW + timedelta(seconds=offset)).isoformat(),
    )
    repository.enqueue_logical_request(request, "provider-a", lease, NOW + timedelta(seconds=offset), QueuePolicy())
    assert (
        repository.claim_next_queued("provider-a", lease, NOW + timedelta(seconds=offset, milliseconds=1)) is not None
    )
    return ProductionPhysicalAttempt(
        physical_attempt_id=f"attempt-{request_id}",
        logical_request_id=request_id,
        sequence_number=1,
        lease_generation=lease.generation,
        created_at=(NOW + timedelta(seconds=offset, milliseconds=2)).isoformat(),
    )


def test_gate_policy_requires_all_approved_scopes() -> None:
    """Fail closed when a hierarchical policy omits a scope."""
    with pytest.raises(ValidationError, match="every approved scope"):
        HierarchicalGatePolicy(
            limits={
                GateScope.GLOBAL: GateLimit(
                    max_concurrency=1, min_interval_seconds=0, requests_per_window=1, window_seconds=1
                )
            }
        )


def test_gate_acquisition_is_atomic_across_all_scopes(tmp_path: Path) -> None:
    """Reserve all eight scopes and create exactly one physical attempt."""
    repository, lease = _runtime(tmp_path)
    attempt = _dequeue(repository, lease, "logical-1", 0)

    reservation = repository.acquire_gates("reservation-1", attempt, KEYS, _policy(), lease, NOW + timedelta(seconds=1))

    assert reservation.physical_attempt_id == attempt.physical_attempt_id
    assert repository.physical_attempt_state(attempt.physical_attempt_id) == ("reserved", 1)
    acquired = [event for event in repository.gate_events("logical-1") if event[0] == "acquired"]
    assert len(acquired) == len(GateScope)


def test_concurrency_block_creates_no_attempt(tmp_path: Path) -> None:
    """Do not partially acquire scopes or create an attempt when one gate blocks."""
    repository, lease = _runtime(tmp_path)
    first = _dequeue(repository, lease, "logical-1", 0)
    repository.acquire_gates("reservation-1", first, KEYS, _policy(), lease, NOW + timedelta(seconds=1))
    second = _dequeue(repository, lease, "logical-2", 2)

    with pytest.raises(RuntimeStorageError, match="gate_blocked:global:concurrency"):
        repository.acquire_gates("reservation-2", second, KEYS, _policy(), lease, NOW + timedelta(seconds=3))

    assert repository.physical_attempt_state(second.physical_attempt_id) is None
    assert repository.gate_events("logical-2") == (("blocked", "global", "concurrency"),)


def test_competing_gate_acquisitions_have_one_winner(tmp_path: Path) -> None:
    """Serialize concurrent owners of the same gate capacity."""
    repository, lease = _runtime(tmp_path)
    attempts = (
        _dequeue(repository, lease, "logical-1", 0),
        _dequeue(repository, lease, "logical-2", 1),
    )

    def acquire(index: int) -> str:
        try:
            repository.acquire_gates(
                f"reservation-{index}", attempts[index], KEYS, _policy(), lease, NOW + timedelta(seconds=2)
            )
        except RuntimeStorageError as error:
            return str(error)
        return "acquired"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(acquire, (0, 1)))

    assert sorted(outcomes) == ["acquired", "gate_blocked:global:concurrency"]


def test_minimum_interval_and_window_boundaries(tmp_path: Path) -> None:
    """Allow acquisition exactly at interval and rolling-window boundaries."""
    repository, lease = _runtime(tmp_path)
    first = _dequeue(repository, lease, "logical-1", 0)
    reservation = repository.acquire_gates(
        "reservation-1", first, KEYS, _policy(interval=5, requests=1, window=5), lease, NOW + timedelta(seconds=1)
    )
    repository.release_gates(reservation, "succeeded", lease, NOW + timedelta(seconds=2))
    early = _dequeue(repository, lease, "logical-2", 3)
    with pytest.raises(RuntimeStorageError, match="minimum_interval"):
        repository.acquire_gates(
            "reservation-2", early, KEYS, _policy(interval=5, requests=1, window=5), lease, NOW + timedelta(seconds=5)
        )
    boundary = _dequeue(repository, lease, "logical-3", 6)

    acquired = repository.acquire_gates(
        "reservation-3", boundary, KEYS, _policy(interval=5, requests=1, window=5), lease, NOW + timedelta(seconds=6)
    )

    assert acquired.logical_request_id == "logical-3"


def test_retry_after_persists_cooldown_without_retry(tmp_path: Path) -> None:
    """Apply the provider cooldown and never create an automatic retry attempt."""
    repository, lease = _runtime(tmp_path)
    first = _dequeue(repository, lease, "logical-1", 0)
    reservation = repository.acquire_gates(
        "reservation-1", first, KEYS, _policy(concurrency=2), lease, NOW + timedelta(seconds=1)
    )
    repository.record_retry_after(reservation, 5, lease, NOW + timedelta(seconds=2))
    second = _dequeue(repository, lease, "logical-2", 3)

    with pytest.raises(RuntimeStorageError, match="gate_blocked:provider:cooldown"):
        repository.acquire_gates(
            "reservation-2", second, KEYS, _policy(concurrency=2), lease, NOW + timedelta(seconds=6)
        )

    assert repository.physical_attempt_state(second.physical_attempt_id) is None
    assert repository.physical_attempt_state(first.physical_attempt_id) == ("failed", 1)
    boundary = _dequeue(repository, lease, "logical-3", 7)
    acquired = repository.acquire_gates(
        "reservation-3", boundary, KEYS, _policy(concurrency=2), lease, NOW + timedelta(seconds=7)
    )
    assert acquired.logical_request_id == "logical-3"


def test_takeover_recovers_active_concurrency_reservation(tmp_path: Path) -> None:
    """Release stale occupancy while preserving limiter starts and audit history."""
    repository, lease = _runtime(tmp_path)
    attempt = _dequeue(repository, lease, "logical-1", 0)
    repository.acquire_gates("reservation-1", attempt, KEYS, _policy(), lease, NOW + timedelta(seconds=1))

    recovered = repository.recover_expired_lease("owner-b", NOW + timedelta(seconds=30), RuntimeLeasePolicy())

    assert recovered.generation == 2
    assert ("recovered", None, "owner_interrupted") in repository.gate_events("logical-1")
