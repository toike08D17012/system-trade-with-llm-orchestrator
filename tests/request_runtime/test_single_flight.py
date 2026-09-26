import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from typing import Literal

import pytest

from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.external_requests import GateScope
from stock_research_llm_orchestrator.requests.production import (
    AdmissionDecision,
    GateKeys,
    GateLimit,
    HierarchicalGatePolicy,
    LogicalResultOutcome,
    ProductionCachePolicy,
    ProductionLogicalRequest,
    ProductionLogicalResult,
    ProductionPhysicalAttempt,
    QueuePolicy,
    RawPublicationIntent,
    RuntimeLease,
    RuntimeLeasePolicy,
)
from stock_research_llm_orchestrator.requests.raw_artifacts import RawArtifactPublisher
from stock_research_llm_orchestrator.requests.storage import (
    DATABASE_FILENAME,
    ProductionRequestRepository,
    RuntimeStorageError,
    initialize_runtime_storage,
)
from stock_research_llm_orchestrator.requests.transport import (
    PhysicalTransportRequest,
    ProductionTransportCoordinator,
    TransportExecutionResult,
    TransportValidationPolicy,
    UntrustedTransportResponse,
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


def _exchange(
    repository: ProductionRequestRepository,
    lease: RuntimeLease,
    request_id: str = "logical-1",
    offset: int = 3,
    outcome: Literal["succeeded", "failed", "unknown"] = "succeeded",
) -> TransportExecutionResult:
    """Execute one synthetic exchange, leaving logical completion to the caller."""
    claim = repository.claim_next_queued("provider-a", lease, NOW + timedelta(seconds=offset))
    assert claim is not None and claim.logical_request_id == request_id
    attempt_id = f"attempt-{request_id}"

    def callback(_request: PhysicalTransportRequest) -> UntrustedTransportResponse:
        if outcome == "unknown":
            raise OSError("synthetic transport interruption")
        return UntrustedTransportResponse(
            status_code=200 if outcome == "succeeded" else 503,
            body=b'{"price":1}',
            media_type="application/json",
            encoding="utf-8",
            final_origin="example-com",
            redirected=False,
        )

    coordinator = ProductionTransportCoordinator(repository, callback, lambda: f"permit-{request_id}")
    limit = GateLimit(max_concurrency=1, min_interval_seconds=0, requests_per_window=10, window_seconds=60)
    return coordinator.execute_exchange(
        PhysicalTransportRequest(
            logical_request_id=request_id,
            physical_attempt_id=attempt_id,
            origin="example-com",
            operation="history",
            resource_key="fixture",
        ),
        ProductionPhysicalAttempt(
            physical_attempt_id=attempt_id,
            logical_request_id=request_id,
            sequence_number=1,
            lease_generation=lease.generation,
            created_at=(NOW + timedelta(seconds=offset)).isoformat(),
        ),
        f"reservation-{request_id}",
        GateKeys(
            egress="default",
            provider="provider-a",
            origin="example-com",
            credential="anonymous",
            operation="history",
            task=claim.task_id,
            role="researcher",
        ),
        HierarchicalGatePolicy(limits={scope: limit for scope in GateScope}),
        TransportValidationPolicy(allowed_media_types=("application/json",), allowed_encodings=("utf-8",)),
        lease,
        NOW + timedelta(seconds=offset),
        NOW + timedelta(seconds=offset + 1),
    )


def _result(
    outcome: LogicalResultOutcome = LogicalResultOutcome.SUCCEEDED,
    request_id: str = "logical-1",
    offset: int = 6,
) -> ProductionLogicalResult:
    return ProductionLogicalResult(
        logical_request_id=request_id,
        outcome=outcome,
        completed_at=(NOW + timedelta(seconds=offset)).isoformat(),
        error_code="fixture_failure"
        if outcome in {LogicalResultOutcome.FAILED, LogicalResultOutcome.UNKNOWN}
        else None,
    )


@pytest.mark.parametrize("outcome", ["succeeded", "failed", "unknown"])
@pytest.mark.parametrize("legacy_api", [False, True])
def test_terminal_results_reach_only_active_consumers(
    tmp_path: Path, outcome: Literal["succeeded", "failed", "unknown"], *, legacy_api: bool
) -> None:
    """Deliver the exact logical result without sending follower requests or reviving cancellations."""
    repository, lease = _runtime(tmp_path)
    _admit(repository, lease, "logical-1", "task-1", 0)
    _admit(repository, lease, "logical-2", "task-2", 1)
    _admit(repository, lease, "logical-3", "task-3", 2)
    repository.cancel_admitted_request("logical-3", "operator_cancelled", lease, NOW + timedelta(seconds=2))
    exchange = _exchange(repository, lease, outcome=outcome)
    assert exchange.status == outcome
    # Physical completion alone must not publish a logical result to followers.
    assert repository.logical_result("logical-2") is None
    assert _admit(repository, lease, "logical-4", "task-4", 5).single_flight_decision == "follower"
    result = _result(LogicalResultOutcome(outcome))
    complete = repository.complete_logical_request if legacy_api else repository.finalize_logical_request
    complete(result, lease, NOW + timedelta(seconds=6))

    for request_id in ("logical-1", "logical-2", "logical-4"):
        assert repository.logical_result(request_id) == result.model_copy(update={"logical_request_id": request_id})
        assert repository.admission_events(request_id)[-1] == ("single_flight_completed", "logical-1")
    cancelled = repository.logical_result("logical-3")
    assert cancelled is not None and cancelled.outcome is LogicalResultOutcome.CANCELLED
    assert repository.committed_raw_references("logical-3") == ()
    with sqlite3.connect(tmp_path / ".runtime" / DATABASE_FILENAME) as connection:
        assert connection.execute("SELECT COUNT(*) FROM physical_attempts").fetchone() == (1,)
        assert connection.execute("SELECT state FROM single_flights").fetchone() == ("terminal",)
    fresh = _admit(repository, lease, "logical-5", "task-5", 7)
    assert fresh.single_flight_decision == "leader" and fresh.cache_decision == "disabled"
    assert repository.logical_result("logical-5") is None


@pytest.mark.parametrize("legacy_api", [False, True])
def test_follower_cannot_finalize_independently(tmp_path: Path, *, legacy_api: bool) -> None:
    """Even the compatibility API cannot detach a follower by writing its own result."""
    repository, lease = _runtime(tmp_path)
    _admit(repository, lease, "logical-1", "task-1", 0)
    _admit(repository, lease, "logical-2", "task-2", 1)
    complete = repository.complete_logical_request if legacy_api else repository.finalize_logical_request
    with pytest.raises(RuntimeStorageError, match="single_flight_leader_required"):
        complete(_result(LogicalResultOutcome.FAILED, "logical-2"), lease, NOW + timedelta(seconds=6))
    assert repository.logical_result("logical-2") is None
    assert repository.logical_request_state("logical-1") == "queued"


@pytest.mark.parametrize("failure_table", ["logical_results", "admission_events"])
def test_completion_fanout_is_atomic(tmp_path: Path, failure_table: str) -> None:
    """Rollback leader, follower, flight, and audit changes if any follower write fails."""
    repository, lease = _runtime(tmp_path)
    _admit(repository, lease, "logical-1", "task-1", 0)
    _admit(repository, lease, "logical-2", "task-2", 1)
    _exchange(repository, lease)
    with sqlite3.connect(tmp_path / ".runtime" / DATABASE_FILENAME) as connection:
        connection.execute(
            f"""CREATE TRIGGER reject_follower BEFORE INSERT ON {failure_table}
                WHEN NEW.logical_request_id = 'logical-2'
                BEGIN SELECT RAISE(ABORT, 'fixture failure'); END"""
        )
    with pytest.raises(RuntimeStorageError, match="logical_result_persistence_failed"):
        repository.finalize_logical_request(_result(), lease, NOW + timedelta(seconds=6))
    for request_id in ("logical-1", "logical-2"):
        assert repository.logical_request_state(request_id) == "queued"
        assert repository.logical_result(request_id) is None
        assert not any(event == "single_flight_completed" for event, _ in repository.admission_events(request_id))
    with sqlite3.connect(tmp_path / ".runtime" / DATABASE_FILENAME) as connection:
        assert connection.execute("SELECT state FROM single_flights").fetchone() == ("active",)
        assert connection.execute("SELECT DISTINCT state, result_owner_id FROM single_flight_consumers").fetchall() == [
            ("active", None)
        ]


def test_historical_raw_owner_survives_next_flight(tmp_path: Path) -> None:
    """Resolve shared raw to its original task even after the fingerprint is reused."""
    repository, lease = _runtime(tmp_path)
    _admit(repository, lease, "logical-1", "task-1", 0)
    _admit(repository, lease, "logical-2", "task-2", 1)
    candidate = _exchange(repository, lease).candidate
    assert candidate is not None
    runs = tmp_path / "runs"
    runs.mkdir(mode=0o700)
    reference = RawArtifactPublisher(runs, repository).publish(
        candidate,
        RawPublicationIntent(
            publication_id="publication-1",
            task_id="task-1",
            logical_request_id="logical-1",
            physical_attempt_id=candidate.physical_attempt_id,
            source_id="fixture",
            operation="history",
            content_sha256=candidate.sha256,
            byte_count=len(candidate.body),
            media_type=candidate.media_type,
            encoding=candidate.encoding,
            raw_schema_id="fixture-raw",
            raw_schema_version=1,
            publication_generation=1,
        ),
        lease,
        NOW + timedelta(seconds=5),
        NOW + timedelta(seconds=5),
        lambda _body: None,
    )
    assert repository.committed_raw_references("logical-2") == ()
    repository.finalize_logical_request(_result(), lease, NOW + timedelta(seconds=6))
    assert repository.committed_raw_references("logical-2") == (reference,)
    _admit(repository, lease, "logical-3", "task-3", 7)
    _admit(repository, lease, "logical-4", "task-4", 8)
    _exchange(repository, lease, "logical-3", 9)
    repository.finalize_logical_request(_result(request_id="logical-3", offset=11), lease, NOW + timedelta(seconds=11))
    reopened = initialize_runtime_storage(tmp_path / ".runtime")
    assert reopened.committed_raw_references("logical-2") == (reference,)
    assert reopened.committed_raw_references("logical-4") == ()
    assert reopened.logical_result("logical-2") == _result(request_id="logical-2")
    assert (runs / reference.task_id / reference.relative_path / "body.bin").read_bytes() == candidate.body


def test_cancelled_and_rejected_fingerprints_can_be_admitted_again(tmp_path: Path) -> None:
    """Reuse inactive flights after cancellation and queue saturation without replacing history."""
    repository, lease = _runtime(tmp_path)
    _admit(repository, lease, "logical-1", "task-1", 0)
    repository.cancel_admitted_request("logical-1", "operator_cancelled", lease, NOW + timedelta(seconds=1))
    assert _admit(repository, lease, "logical-2", "task-2", 2).single_flight_decision == "leader"
    rejected = _request("rejected", "task-3").model_copy(update={"request_fingerprint": "b" * 64})
    with pytest.raises(RuntimeStorageError, match="queue_full"):
        repository.admit_logical_request(
            rejected,
            "provider-a",
            ProductionCachePolicy(applicable=True),
            lease,
            NOW + timedelta(seconds=3),
            QueuePolicy(global_limit=1, rate_domain_limit=1, task_rate_domain_limit=1),
        )
    fresh = rejected.model_copy(update={"logical_request_id": "fresh"})
    assert (
        repository.admit_logical_request(
            fresh,
            "provider-a",
            ProductionCachePolicy(applicable=True),
            lease,
            NOW + timedelta(seconds=4),
            QueuePolicy(),
        ).single_flight_decision
        == "leader"
    )
    rejected_result = repository.logical_result("rejected")
    assert rejected_result is not None and rejected_result.error_code == "queue_full"


@pytest.mark.parametrize("stage", ["started", "between_attempts", "unknown_exchange"])
def test_recovery_propagates_interrupted_flight(tmp_path: Path, stage: str) -> None:
    """Release all waiting consumers on recovery without scheduling a second physical send."""
    repository, lease = _runtime(tmp_path)
    _admit(repository, lease, "logical-1", "task-1", 0)
    _admit(repository, lease, "logical-2", "task-2", 1)
    if stage == "started":
        assert repository.claim_next_queued("provider-a", lease, NOW + timedelta(seconds=2)) is not None
        repository.add_physical_attempt(
            ProductionPhysicalAttempt(
                physical_attempt_id="attempt-logical-1",
                logical_request_id="logical-1",
                sequence_number=1,
                lease_generation=lease.generation,
                created_at=(NOW + timedelta(seconds=3)).isoformat(),
            )
        )
        repository.mark_physical_attempt_started("attempt-logical-1", lease, NOW + timedelta(seconds=3))
    else:
        _exchange(repository, lease, outcome="unknown" if stage == "unknown_exchange" else "succeeded")
    recovered = repository.recover_expired_lease("owner-b", NOW + timedelta(seconds=31), RuntimeLeasePolicy())
    result = repository.logical_result("logical-1")
    assert result is not None
    expected = LogicalResultOutcome.FAILED if stage == "between_attempts" else LogicalResultOutcome.UNKNOWN
    assert result.outcome is expected
    assert repository.logical_result("logical-2") == result.model_copy(update={"logical_request_id": "logical-2"})
    assert repository.claim_next_queued("provider-a", recovered, NOW + timedelta(seconds=32)) is None
    assert _admit(repository, recovered, "logical-3", "task-3", 33).single_flight_decision == "leader"
    with pytest.raises(RuntimeStorageError, match="stale_runtime_lease"):
        repository.finalize_logical_request(_result(), lease, NOW + timedelta(seconds=34))


def _legacy_terminal_leader(tmp_path: Path, outcome: str = "succeeded") -> None:
    """Emulate a v7 result persisted before flight completion was implemented."""
    with sqlite3.connect(tmp_path / ".runtime" / DATABASE_FILENAME) as connection:
        connection.execute("UPDATE logical_requests SET state = ? WHERE logical_request_id = 'logical-1'", (outcome,))
        connection.execute(
            "INSERT INTO logical_results VALUES ('logical-1', ?, ?, ?)",
            (outcome, (NOW + timedelta(seconds=6)).isoformat(), "fixture_failure" if outcome == "failed" else None),
        )


@pytest.mark.parametrize("repair_path", ["admission", "recovery", "acquisition"])
def test_terminal_legacy_leader_is_repaired_without_overwriting_result(tmp_path: Path, repair_path: str) -> None:
    """Retain v7's result and join audit while finishing its stranded followers."""
    repository, lease = _runtime(tmp_path)
    _admit(repository, lease, "logical-1", "task-1", 0)
    _admit(repository, lease, "logical-2", "task-2", 1)
    _exchange(repository, lease)
    _legacy_terminal_leader(tmp_path)
    original_result = repository.logical_result("logical-1")
    original_events = repository.admission_events("logical-1")
    if repair_path == "admission":
        assert _admit(repository, lease, "logical-3", "task-3", 7).single_flight_decision == "leader"
    elif repair_path == "recovery":
        repository.recover_expired_lease("owner-b", NOW + timedelta(seconds=31), RuntimeLeasePolicy())
    else:
        repository.release_lease(lease, NOW + timedelta(seconds=7))
        repository.acquire_lease("owner-b", NOW + timedelta(seconds=8), RuntimeLeasePolicy())
    assert repository.logical_result("logical-1") == original_result
    assert repository.logical_result("logical-2") == _result(request_id="logical-2")
    assert repository.admission_events("logical-1") == (*original_events, ("single_flight_completed", "logical-1"))


@pytest.mark.parametrize("corruption", ["missing_result", "missing_audit", "outcome_mismatch", "active_attempt"])
def test_legacy_repair_rejects_inconsistent_history(tmp_path: Path, corruption: str) -> None:
    """Never infer successful shared outcomes from broken legacy provenance."""
    repository, lease = _runtime(tmp_path)
    _admit(repository, lease, "logical-1", "task-1", 0)
    _admit(repository, lease, "logical-2", "task-2", 1)
    _exchange(repository, lease)
    _legacy_terminal_leader(tmp_path)
    with sqlite3.connect(tmp_path / ".runtime" / DATABASE_FILENAME) as connection:
        if corruption == "missing_result":
            connection.execute("DELETE FROM logical_results")
        elif corruption == "missing_audit":
            connection.execute("DELETE FROM admission_events WHERE logical_request_id = 'logical-2'")
        elif corruption == "outcome_mismatch":
            connection.execute("UPDATE logical_requests SET state = 'failed' WHERE logical_request_id = 'logical-1'")
        else:
            connection.execute("UPDATE physical_attempts SET state = 'started'")
    with pytest.raises(RuntimeStorageError, match="single_flight_audit_gap|logical_request_has_active_attempt"):
        _admit(repository, lease, "logical-3", "task-3", 7)
    assert repository.logical_request_state("logical-3") is None
    assert repository.logical_result("logical-2") is None
    with sqlite3.connect(tmp_path / ".runtime" / DATABASE_FILENAME) as connection:
        assert connection.execute("SELECT state FROM single_flights").fetchone() == ("active",)


def test_schema_v7_migration_preserves_legacy_consumers_and_results(tmp_path: Path) -> None:
    """Migrate populated v7 state atomically, then repair only under a valid lease."""
    repository, lease = _runtime(tmp_path)
    _admit(repository, lease, "logical-1", "task-1", 0)
    _admit(repository, lease, "logical-2", "task-2", 1)
    _admit(repository, lease, "logical-3", "task-3", 2)
    repository.cancel_admitted_request("logical-3", "operator_cancelled", lease, NOW + timedelta(seconds=2))
    _exchange(repository, lease)
    _legacy_terminal_leader(tmp_path)
    database = tmp_path / ".runtime" / DATABASE_FILENAME
    with sqlite3.connect(database) as connection:
        before = connection.execute("SELECT * FROM logical_results ORDER BY logical_request_id").fetchall()
        audit = connection.execute("SELECT * FROM admission_events").fetchall()
        connection.executescript("""
            BEGIN IMMEDIATE;
            CREATE TABLE consumers_v7 (
                logical_request_id TEXT PRIMARY KEY REFERENCES logical_requests(logical_request_id),
                request_fingerprint TEXT NOT NULL REFERENCES single_flights(request_fingerprint),
                role TEXT NOT NULL CHECK (role IN ('leader', 'follower')),
                state TEXT NOT NULL CHECK (state IN ('active', 'cancelled')),
                cache_decision TEXT NOT NULL CHECK (cache_decision IN ('disabled', 'not_applicable')),
                rate_domain TEXT NOT NULL,
                joined_at TEXT NOT NULL
            );
            INSERT INTO consumers_v7 SELECT logical_request_id, request_fingerprint, role, state,
                cache_decision, rate_domain, joined_at FROM single_flight_consumers;
            DROP TABLE single_flight_consumers;
            ALTER TABLE consumers_v7 RENAME TO single_flight_consumers;
            UPDATE schema_metadata SET schema_version = 7;
            PRAGMA user_version = 7;
            COMMIT;
        """)
        consumers = connection.execute("SELECT * FROM single_flight_consumers ORDER BY logical_request_id").fetchall()
    migrated = initialize_runtime_storage(tmp_path / ".runtime")
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (8,)
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("SELECT schema_version FROM schema_metadata").fetchone() == (8,)
        assert connection.execute("SELECT * FROM logical_results ORDER BY logical_request_id").fetchall() == before
        assert connection.execute("SELECT * FROM admission_events").fetchall() == audit
        assert connection.execute("SELECT * FROM single_flight_consumers ORDER BY logical_request_id").fetchall() == [
            (*consumer, None) for consumer in consumers
        ]
    assert _admit(migrated, lease, "logical-4", "task-4", 7).single_flight_decision == "leader"
    assert migrated.logical_result("logical-2") == _result(request_id="logical-2")
    cancelled = migrated.logical_result("logical-3")
    assert cancelled is not None and cancelled.outcome is LogicalResultOutcome.CANCELLED


def test_promoted_leader_owns_completed_results(tmp_path: Path) -> None:
    """Attribute surviving consumers to the promoted owner, keeping the original cancelled."""
    repository, lease = _runtime(tmp_path)
    for i in range(1, 4):
        _admit(repository, lease, f"logical-{i}", f"task-{i}", i - 1)
    assert (
        repository.cancel_admitted_request("logical-1", "operator_cancelled", lease, NOW + timedelta(seconds=3))
        == "logical-2"
    )
    _exchange(repository, lease, "logical-2", 4)
    repository.finalize_logical_request(_result(request_id="logical-2"), lease, NOW + timedelta(seconds=6))
    assert repository.logical_request_state("logical-1") == "cancelled"
    assert repository.logical_result("logical-3") == _result(request_id="logical-3")
    assert repository.admission_events("logical-3")[-1] == ("single_flight_completed", "logical-2")


@pytest.mark.parametrize("legacy_api", [False, True])
def test_finalization_requires_terminal_attempt_and_valid_lease(tmp_path: Path, *, legacy_api: bool) -> None:
    """Prevent in-flight or stale-owner completion from reaching waiting consumers."""
    repository, lease = _runtime(tmp_path)
    _admit(repository, lease, "logical-1", "task-1", 0)
    _admit(repository, lease, "logical-2", "task-2", 1)
    repository.add_physical_attempt(
        ProductionPhysicalAttempt(
            physical_attempt_id="attempt-1",
            logical_request_id="logical-1",
            sequence_number=1,
            lease_generation=lease.generation,
            created_at=NOW.isoformat(),
        )
    )
    complete = repository.complete_logical_request if legacy_api else repository.finalize_logical_request
    with pytest.raises(RuntimeStorageError, match="logical_request_has_active_attempt"):
        complete(_result(LogicalResultOutcome.FAILED), lease, NOW + timedelta(seconds=6))
    repository.release_lease(lease, NOW + timedelta(seconds=7))
    with pytest.raises(RuntimeStorageError, match="stale_runtime_lease"):
        complete(_result(LogicalResultOutcome.FAILED), lease, NOW + timedelta(seconds=8))
    assert repository.logical_result("logical-1") is None
    assert repository.logical_result("logical-2") is None


def test_source_validation_failure_releases_followers_without_raw(tmp_path: Path) -> None:
    """A valid HTTP response must not become shared success when source parsing fails."""
    repository, lease = _runtime(tmp_path)
    _admit(repository, lease, "logical-1", "task-1", 0)
    _admit(repository, lease, "logical-2", "task-2", 1)
    candidate = _exchange(repository, lease).candidate
    assert candidate is not None
    runs = tmp_path / "runs"
    runs.mkdir(mode=0o700)
    intent = RawPublicationIntent(
        publication_id="publication-1",
        task_id="task-1",
        logical_request_id="logical-1",
        physical_attempt_id=candidate.physical_attempt_id,
        source_id="fixture",
        operation="history",
        content_sha256=candidate.sha256,
        byte_count=len(candidate.body),
        media_type=candidate.media_type,
        encoding=candidate.encoding,
        raw_schema_id="fixture-raw",
        raw_schema_version=1,
        publication_generation=1,
    )

    def reject_source(_body: bytes) -> None:
        raise ValueError("source_validation_failed")

    with pytest.raises(ValueError, match="source_validation_failed"):
        RawArtifactPublisher(runs, repository).publish(
            candidate, intent, lease, NOW + timedelta(seconds=5), NOW + timedelta(seconds=5), reject_source
        )
    result = _result(LogicalResultOutcome.FAILED)
    repository.finalize_logical_request(result, lease, NOW + timedelta(seconds=6))
    assert repository.logical_result("logical-2") == result.model_copy(update={"logical_request_id": "logical-2"})
    assert repository.committed_raw_references("logical-2") == ()
    assert repository.raw_publication_state("publication-1") == ("failed", None)
    assert _admit(repository, lease, "logical-3", "task-3", 7).single_flight_decision == "leader"


def test_presend_failure_removes_shared_work_from_queue(tmp_path: Path) -> None:
    """Finalize rejected pre-send work without leaving a schedulable leader behind."""
    repository, lease = _runtime(tmp_path)
    _admit(repository, lease, "logical-1", "task-1", 0)
    _admit(repository, lease, "logical-2", "task-2", 1)
    result = _result(LogicalResultOutcome.FAILED)
    repository.finalize_logical_request(result, lease, NOW + timedelta(seconds=6))
    assert repository.logical_result("logical-2") == result.model_copy(update={"logical_request_id": "logical-2"})
    assert repository.claim_next_queued("provider-a", lease, NOW + timedelta(seconds=7)) is None
    assert repository.admission_events("logical-1")[-1] == ("single_flight_completed", "logical-1")


def test_admission_racing_completion_never_strands_consumer(tmp_path: Path) -> None:
    """Serialize two real connections so a racing consumer either completes or leads new work."""
    repository, lease = _runtime(tmp_path)
    _admit(repository, lease, "logical-1", "task-1", 0)
    _exchange(repository, lease)
    barrier = Barrier(2, timeout=5)

    def finish() -> None:
        barrier.wait()
        repository.finalize_logical_request(_result(), lease, NOW + timedelta(seconds=6))

    def join() -> AdmissionDecision:
        barrier.wait()
        return _admit(repository, lease, "logical-2", "task-2", 6)

    with ThreadPoolExecutor(max_workers=2) as executor:
        completion = executor.submit(finish)
        admission = executor.submit(join)
        completion.result(timeout=10)
        decision = admission.result(timeout=10)
    if decision.single_flight_decision == "follower":
        assert repository.logical_result("logical-2") == _result(request_id="logical-2")
        assert repository.claim_next_queued("provider-a", lease, NOW + timedelta(seconds=7)) is None
    else:
        assert decision.leader_logical_request_id == "logical-2"
        assert repository.logical_result("logical-2") is None
        claim = repository.claim_next_queued("provider-a", lease, NOW + timedelta(seconds=7))
        assert claim is not None and claim.logical_request_id == "logical-2"
