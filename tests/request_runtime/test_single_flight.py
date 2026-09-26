from datetime import UTC, datetime, timedelta
from pathlib import Path

from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.external_requests import GateScope
from stock_research_llm_orchestrator.requests.production import (
    AdmissionDecision,
    GateKeys,
    GateLimit,
    HierarchicalGatePolicy,
    ProductionCachePolicy,
    ProductionLogicalRequest,
    ProductionPhysicalAttempt,
    QueuePolicy,
    RuntimeLease,
    RuntimeLeasePolicy,
)
from stock_research_llm_orchestrator.requests.storage import (
    ProductionRequestRepository,
    initialize_runtime_storage,
)


NOW = datetime(2026, 9, 22, tzinfo=UTC)
FINGERPRINT = "a" * 64


def _runtime(tmp_path: Path) -> tuple[ProductionRequestRepository, RuntimeLease]:
    root = tmp_path / ".runtime"
    root.mkdir(mode=0o700)
    repository = initialize_runtime_storage(root)
    return repository, repository.acquire_lease("owner-a", NOW, RuntimeLeasePolicy())


def _request(request_id: str, task_id: str) -> ProductionLogicalRequest:
    return ProductionLogicalRequest(
        logical_request_id=request_id,
        task_id=task_id,
        source_id="fixture",
        operation="history",
        request_fingerprint=FINGERPRINT,
        source_approval_version=1,
        source_profile_version=1,
        credential_scope_alias=None,
        egress_scope="default",
        created_at=NOW.isoformat(),
    )


def _admit(
    repository: ProductionRequestRepository,
    lease: RuntimeLease,
    request_id: str,
    task_id: str,
    offset: int,
    *,
    applicable: bool = True,
) -> AdmissionDecision:
    return repository.admit_logical_request(
        _request(request_id, task_id),
        "provider-a",
        ProductionCachePolicy(applicable=applicable),
        lease,
        NOW + timedelta(seconds=offset),
        QueuePolicy(),
    )


def test_cache_decision_precedes_single_flight_and_queue(tmp_path: Path) -> None:
    """Persist cache eligibility before selecting one physical leader."""
    repository, lease = _runtime(tmp_path)

    leader = _admit(repository, lease, "logical-1", "task-1", 0, applicable=True)
    follower = _admit(repository, lease, "logical-2", "task-2", 1, applicable=False)

    assert leader.cache_decision == "disabled"
    assert leader.single_flight_decision == "leader"
    assert follower.cache_decision == "not_applicable"
    assert follower.single_flight_decision == "follower"
    assert follower.leader_logical_request_id == "logical-1"
    assert repository.admission_events("logical-1")[:2] == (
        ("cache_disabled", None),
        ("single_flight_leader", "logical-1"),
    )
    assert repository.admission_events("logical-2")[:2] == (
        ("cache_not_applicable", None),
        ("single_flight_follower", "logical-1"),
    )


def test_follower_has_no_queue_entry_or_physical_attempt(tmp_path: Path) -> None:
    """Only the leader may proceed toward one physical send."""
    repository, lease = _runtime(tmp_path)
    _admit(repository, lease, "logical-1", "task-1", 0)
    _admit(repository, lease, "logical-2", "task-2", 1)

    claimed = repository.claim_next_queued("provider-a", lease, NOW + timedelta(seconds=2))

    assert claimed is not None and claimed.logical_request_id == "logical-1"
    assert repository.claim_next_queued("provider-a", lease, NOW + timedelta(seconds=3)) is None
    assert repository.physical_attempt_state("attempt-logical-2") is None


def test_cancelling_follower_does_not_cancel_leader(tmp_path: Path) -> None:
    """Detach one follower without changing the shared leader."""
    repository, lease = _runtime(tmp_path)
    _admit(repository, lease, "logical-1", "task-1", 0)
    _admit(repository, lease, "logical-2", "task-2", 1)

    promoted = repository.cancel_admitted_request("logical-2", "operator_cancelled", lease, NOW + timedelta(seconds=2))

    assert promoted is None
    assert repository.logical_request_state("logical-2") == "cancelled"
    assert repository.logical_request_state("logical-1") == "queued"
    claimed = repository.claim_next_queued("provider-a", lease, NOW + timedelta(seconds=3))
    assert claimed is not None and claimed.logical_request_id == "logical-1"


def test_cancelling_unsent_leader_promotes_oldest_follower(tmp_path: Path) -> None:
    """Transactionally replace a queued leader with its oldest consumer."""
    repository, lease = _runtime(tmp_path)
    _admit(repository, lease, "logical-1", "task-1", 0)
    _admit(repository, lease, "logical-2", "task-2", 1)
    _admit(repository, lease, "logical-3", "task-3", 2)

    promoted = repository.cancel_admitted_request("logical-1", "operator_cancelled", lease, NOW + timedelta(seconds=3))
    claimed = repository.claim_next_queued("provider-a", lease, NOW + timedelta(seconds=4))

    assert promoted == "logical-2"
    assert repository.logical_request_state("logical-1") == "cancelled"
    assert claimed is not None and claimed.logical_request_id == "logical-2"
    assert ("leader_promoted", "logical-1") in repository.admission_events("logical-2")


def test_cancelling_last_queued_consumer_terminates_flight(tmp_path: Path) -> None:
    """Cancel a leader with no consumers without leaving schedulable work."""
    repository, lease = _runtime(tmp_path)
    _admit(repository, lease, "logical-1", "task-1", 0)

    promoted = repository.cancel_admitted_request("logical-1", "operator_cancelled", lease, NOW + timedelta(seconds=1))

    assert promoted is None
    assert repository.logical_request_state("logical-1") == "cancelled"
    assert repository.claim_next_queued("provider-a", lease, NOW + timedelta(seconds=2)) is None


def test_cancelling_started_leader_marks_every_consumer_unknown(tmp_path: Path) -> None:
    """Never treat an interrupted in-flight single-flight request as cancelled success."""
    repository, lease = _runtime(tmp_path)
    _admit(repository, lease, "logical-1", "task-1", 0)
    _admit(repository, lease, "logical-2", "task-2", 1)
    assert repository.claim_next_queued("provider-a", lease, NOW + timedelta(seconds=2)) is not None
    attempt = ProductionPhysicalAttempt(
        physical_attempt_id="attempt-1",
        logical_request_id="logical-1",
        sequence_number=1,
        lease_generation=1,
        created_at=(NOW + timedelta(seconds=2)).isoformat(),
    )
    limit = GateLimit(
        max_concurrency=1,
        min_interval_seconds=0,
        requests_per_window=10,
        window_seconds=60,
    )
    reservation = repository.acquire_gates(
        "reservation-1",
        attempt,
        GateKeys(
            egress="default",
            provider="provider-a",
            origin="example-com",
            credential="anonymous",
            operation="history",
            task="task-1",
            role="researcher",
        ),
        HierarchicalGatePolicy(limits={scope: limit for scope in GateScope}),
        lease,
        NOW + timedelta(seconds=3),
    )
    repository.mark_physical_attempt_started(reservation.physical_attempt_id, lease, NOW + timedelta(seconds=4))

    repository.cancel_admitted_request("logical-1", "operator_cancelled", lease, NOW + timedelta(seconds=5))

    assert repository.physical_attempt_state("attempt-1") == ("unknown", 1)
    assert repository.logical_request_state("logical-1") == "failed"
    assert repository.logical_request_state("logical-2") == "failed"
    assert ("in_flight_cancelled", "operator_cancelled") in repository.admission_events("logical-2")


def test_cancelling_between_attempts_never_promotes_follower(tmp_path: Path) -> None:
    """Do not hand a request to another leader after any physical send completed."""
    repository, lease = _runtime(tmp_path)
    _admit(repository, lease, "logical-1", "task-1", 0)
    _admit(repository, lease, "logical-2", "task-2", 1)
    assert repository.claim_next_queued("provider-a", lease, NOW + timedelta(seconds=2)) is not None
    attempt = ProductionPhysicalAttempt(
        physical_attempt_id="attempt-1",
        logical_request_id="logical-1",
        sequence_number=1,
        lease_generation=1,
        created_at=(NOW + timedelta(seconds=2)).isoformat(),
    )
    limit = GateLimit(max_concurrency=1, min_interval_seconds=0, requests_per_window=10, window_seconds=60)
    reservation = repository.acquire_gates(
        "reservation-1",
        attempt,
        GateKeys(
            egress="default",
            provider="provider-a",
            origin="example-com",
            credential="anonymous",
            operation="history",
            task="task-1",
            role="researcher",
        ),
        HierarchicalGatePolicy(limits={scope: limit for scope in GateScope}),
        lease,
        NOW + timedelta(seconds=3),
    )
    repository.mark_physical_attempt_started("attempt-1", lease, NOW + timedelta(seconds=3))
    repository.release_gates(reservation, "failed", lease, NOW + timedelta(seconds=4))

    promoted = repository.cancel_admitted_request("logical-1", "operator_cancelled", lease, NOW + timedelta(seconds=5))

    assert promoted is None
    assert repository.logical_request_state("logical-1") == "failed"
    assert repository.logical_request_state("logical-2") == "failed"
    assert repository.claim_next_queued("provider-a", lease, NOW + timedelta(seconds=6)) is None
