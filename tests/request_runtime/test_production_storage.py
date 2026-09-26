import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest
from pydantic import ValidationError

from stock_research_llm_orchestrator.requests.production import (
    LogicalResultOutcome,
    PhysicalAttemptState,
    ProductionLogicalRequest,
    ProductionLogicalResult,
    ProductionPhysicalAttempt,
    QueuePolicy,
    RuntimeLease,
    RuntimeLeasePolicy,
)
from stock_research_llm_orchestrator.requests.storage import (
    DATABASE_FILENAME,
    ProductionRequestRepository,
    RuntimeStorageError,
    initialize_runtime_storage,
)


def _runtime_root(tmp_path: Path) -> Path:
    root = tmp_path / ".runtime"
    root.mkdir(mode=0o700)
    return root


def _drop_phase_4b_tables(connection: sqlite3.Connection) -> None:
    for table in (
        "gate_events",
        "gate_starts",
        "gate_reservation_scopes",
        "gate_reservations",
        "gate_state",
    ):
        connection.execute(f"DROP TABLE {table}")


def _drop_phase_4c_tables(connection: sqlite3.Connection) -> None:
    for table in ("admission_events", "single_flight_consumers", "single_flights"):
        connection.execute(f"DROP TABLE {table}")


def _drop_phase_6_tables(connection: sqlite3.Connection) -> None:
    connection.execute("DROP TABLE raw_publications")


def _drop_phase_7_columns(connection: sqlite3.Connection) -> None:
    connection.execute("ALTER TABLE physical_attempts DROP COLUMN raw_eligible")


def _logical_request() -> ProductionLogicalRequest:
    return ProductionLogicalRequest(
        logical_request_id="logical-1",
        task_id="task-1",
        source_id="fixture",
        operation="history",
        request_fingerprint="a" * 64,
        source_approval_version=1,
        source_profile_version=1,
        credential_scope_alias=None,
        egress_scope="default",
        created_at="2026-09-21T00:00:00+00:00",
    )


def _expected_lineage() -> dict[str, object]:
    return {
        "task_id": "task-1",
        "source_id": "fixture",
        "operation": "history",
        "source_approval_version": 1,
        "source_profile_version": 1,
        "credential_scope_alias": None,
        "egress_scope": "default",
        "rate_domain": "provider-a",
    }


def _assert_lineage(
    repository: ProductionRequestRepository,
    lease: RuntimeLease,
    now: datetime,
    lineage: dict[str, object],
) -> None:
    repository.assert_claimed_logical_request(
        "logical-1",
        task_id=cast(str, lineage["task_id"]),
        source_id=cast(str, lineage["source_id"]),
        operation=cast(str, lineage["operation"]),
        source_approval_version=cast(int, lineage["source_approval_version"]),
        source_profile_version=cast(int, lineage["source_profile_version"]),
        credential_scope_alias=cast(str | None, lineage["credential_scope_alias"]),
        egress_scope=cast(str, lineage["egress_scope"]),
        rate_domain=cast(str, lineage["rate_domain"]),
        lease=lease,
        now=now,
    )


def test_initialize_runtime_storage_creates_versioned_private_database(tmp_path: Path) -> None:
    """Create the exact current schema with owner-only database permissions."""
    root = _runtime_root(tmp_path)

    initialize_runtime_storage(root)

    database = root / DATABASE_FILENAME
    assert database.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (7,)
        assert connection.execute("SELECT schema_owner, schema_version FROM schema_metadata").fetchone() == (
            "production-request-coordinator",
            7,
        )
        columns = {
            row[1]
            for table in ("logical_requests", "physical_attempts", "runtime_lease")
            for row in connection.execute(f"PRAGMA table_info({table})")
        }
    assert "payload" not in columns
    assert "response_body" not in columns
    assert "credential" not in columns


@pytest.mark.parametrize("mode", [0o755, 0o770, 0o777])
def test_initialize_runtime_storage_rejects_unsafe_root_mode(tmp_path: Path, mode: int) -> None:
    """Reject runtime roots accessible beyond their owner."""
    root = _runtime_root(tmp_path)
    root.chmod(mode)

    with pytest.raises(RuntimeStorageError, match="invalid_runtime_root_permissions"):
        initialize_runtime_storage(root)


def test_initialize_runtime_storage_rejects_symlink_root(tmp_path: Path) -> None:
    """Reject a symlink in place of the trusted runtime root."""
    target = _runtime_root(tmp_path)
    link = tmp_path / "runtime-link"
    link.symlink_to(target, target_is_directory=True)

    with pytest.raises(RuntimeStorageError, match="invalid_runtime_root"):
        initialize_runtime_storage(link)


def test_initialize_runtime_storage_rejects_unknown_schema(tmp_path: Path) -> None:
    """Fail closed when the on-disk schema is newer or unknown."""
    root = _runtime_root(tmp_path)
    database = root / DATABASE_FILENAME
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA user_version = 99")
    database.chmod(0o600)

    with pytest.raises(RuntimeStorageError, match="unsupported_runtime_schema"):
        initialize_runtime_storage(root)


def test_initialize_runtime_storage_reopens_current_schema(tmp_path: Path) -> None:
    """Reopen the exact current schema without recreating durable state."""
    root = _runtime_root(tmp_path)
    first = initialize_runtime_storage(root)
    first.add_logical_request(_logical_request())

    reopened = initialize_runtime_storage(root)

    assert reopened.logical_request_state("logical-1") == "queued"


def test_initialize_runtime_storage_migrates_phase_3a_schema(tmp_path: Path) -> None:
    """Migrate the committed Phase 3A schema without discarding runtime state."""
    root = _runtime_root(tmp_path)
    repository = initialize_runtime_storage(root)
    repository.add_logical_request(_logical_request())
    database = root / DATABASE_FILENAME
    with sqlite3.connect(database) as connection:
        _drop_phase_7_columns(connection)
        _drop_phase_6_tables(connection)
        _drop_phase_4c_tables(connection)
        _drop_phase_4b_tables(connection)
        connection.execute("DROP TABLE queue_events")
        connection.execute("DROP TABLE provider_queue_cursors")
        connection.execute("DROP TABLE queue_entries")
        connection.execute("ALTER TABLE runtime_lease DROP COLUMN active")
        connection.execute("UPDATE schema_metadata SET schema_version = 1")
        connection.execute("PRAGMA user_version = 1")

    migrated = initialize_runtime_storage(root)

    assert migrated.logical_request_state("logical-1") == "queued"
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (7,)
        columns = {row[1] for row in connection.execute("PRAGMA table_info(runtime_lease)")}
    assert "active" in columns


def test_initialize_runtime_storage_migrates_phase_3b_schema(tmp_path: Path) -> None:
    """Add durable queue state to an existing Phase 3B database."""
    root = _runtime_root(tmp_path)
    repository = initialize_runtime_storage(root)
    repository.add_logical_request(_logical_request())
    database = root / DATABASE_FILENAME
    with sqlite3.connect(database) as connection:
        _drop_phase_7_columns(connection)
        _drop_phase_6_tables(connection)
        _drop_phase_4c_tables(connection)
        _drop_phase_4b_tables(connection)
        connection.execute("DROP TABLE queue_events")
        connection.execute("DROP TABLE provider_queue_cursors")
        connection.execute("DROP TABLE queue_entries")
        connection.execute("UPDATE schema_metadata SET schema_version = 2")
        connection.execute("PRAGMA user_version = 2")

    migrated = initialize_runtime_storage(root)

    assert migrated.logical_request_state("logical-1") == "queued"
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (7,)
        tables = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'queue_%'")
        }
    assert tables == {"queue_entries", "queue_events"}


def test_initialize_runtime_storage_migrates_phase_4a_schema(tmp_path: Path) -> None:
    """Add persistent gate state to an existing Phase 4A database."""
    root = _runtime_root(tmp_path)
    initialize_runtime_storage(root)
    database = root / DATABASE_FILENAME
    with sqlite3.connect(database) as connection:
        _drop_phase_7_columns(connection)
        _drop_phase_6_tables(connection)
        _drop_phase_4c_tables(connection)
        _drop_phase_4b_tables(connection)
        connection.execute("UPDATE schema_metadata SET schema_version = 3")
        connection.execute("PRAGMA user_version = 3")

    initialize_runtime_storage(root)

    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (7,)
        tables = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'gate_%'")
        }
    assert tables == {
        "gate_events",
        "gate_reservation_scopes",
        "gate_reservations",
        "gate_starts",
        "gate_state",
    }


def test_initialize_runtime_storage_migrates_phase_4b_schema(tmp_path: Path) -> None:
    """Add single-flight state to an existing Phase 4B database."""
    root = _runtime_root(tmp_path)
    initialize_runtime_storage(root)
    database = root / DATABASE_FILENAME
    with sqlite3.connect(database) as connection:
        _drop_phase_7_columns(connection)
        _drop_phase_6_tables(connection)
        _drop_phase_4c_tables(connection)
        connection.execute("UPDATE schema_metadata SET schema_version = 4")
        connection.execute("PRAGMA user_version = 4")

    initialize_runtime_storage(root)

    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (7,)
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'single_flight%'"
            )
        }
    assert tables == {"single_flight_consumers", "single_flights"}


def test_initialize_runtime_storage_migrates_phase_5_schema(tmp_path: Path) -> None:
    """Add raw publication state to an existing controlled transport database."""
    root = _runtime_root(tmp_path)
    initialize_runtime_storage(root)
    database = root / DATABASE_FILENAME
    with sqlite3.connect(database) as connection:
        _drop_phase_7_columns(connection)
        _drop_phase_6_tables(connection)
        connection.execute("UPDATE schema_metadata SET schema_version = 5")
        connection.execute("PRAGMA user_version = 5")

    initialize_runtime_storage(root)

    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (7,)
        table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'raw_publications'"
        ).fetchone()
    assert table == ("raw_publications",)


def test_initialize_runtime_storage_migrates_phase_6_raw_eligibility(tmp_path: Path) -> None:
    """Add an explicit eligibility marker without trusting historical failed attempts."""
    root = _runtime_root(tmp_path)
    repository = initialize_runtime_storage(root)
    repository.add_logical_request(_logical_request())
    repository.add_physical_attempt(
        ProductionPhysicalAttempt(
            physical_attempt_id="attempt-1",
            logical_request_id="logical-1",
            sequence_number=1,
            lease_generation=1,
            created_at="2026-09-21T00:00:01+00:00",
        )
    )
    database = root / DATABASE_FILENAME
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE physical_attempts SET state = 'failed'")
        _drop_phase_7_columns(connection)
        connection.execute("UPDATE schema_metadata SET schema_version = 6")
        connection.execute("PRAGMA user_version = 6")

    initialize_runtime_storage(root)

    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (7,)
        assert connection.execute(
            "SELECT raw_eligible FROM physical_attempts WHERE physical_attempt_id = 'attempt-1'"
        ).fetchone() == (0,)


def test_initialize_runtime_storage_rejects_schema_metadata_mismatch(tmp_path: Path) -> None:
    """Fail closed when version metadata disagrees with the SQLite pragma."""
    root = _runtime_root(tmp_path)
    initialize_runtime_storage(root)
    database = root / DATABASE_FILENAME
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE schema_metadata SET schema_owner = 'unexpected'")

    with pytest.raises(RuntimeStorageError, match="runtime_schema_metadata_mismatch"):
        initialize_runtime_storage(root)


def test_repository_persists_logical_request_before_attempt(tmp_path: Path) -> None:
    """Require durable logical intent before reserving physical work."""
    repository = initialize_runtime_storage(_runtime_root(tmp_path))
    request = _logical_request()
    repository.add_logical_request(request)
    attempt = ProductionPhysicalAttempt(
        physical_attempt_id="attempt-1",
        logical_request_id=request.logical_request_id,
        sequence_number=1,
        lease_generation=1,
        created_at="2026-09-21T00:00:01+00:00",
    )

    repository.add_physical_attempt(attempt)

    assert repository.logical_request_state(request.logical_request_id) == "queued"


def test_repository_rejects_attempt_without_logical_request(tmp_path: Path) -> None:
    """Reject orphan physical attempts through the foreign key boundary."""
    repository = initialize_runtime_storage(_runtime_root(tmp_path))
    attempt = ProductionPhysicalAttempt(
        physical_attempt_id="attempt-1",
        logical_request_id="missing-logical",
        sequence_number=1,
        lease_generation=1,
        created_at="2026-09-21T00:00:01+00:00",
    )

    with pytest.raises(RuntimeStorageError, match="physical_attempt_persistence_failed"):
        repository.add_physical_attempt(attempt)


def test_repository_rejects_duplicate_attempt_sequence(tmp_path: Path) -> None:
    """Keep attempt sequence numbers unique within a logical request."""
    repository = initialize_runtime_storage(_runtime_root(tmp_path))
    request = _logical_request()
    repository.add_logical_request(request)
    first = ProductionPhysicalAttempt(
        physical_attempt_id="attempt-1",
        logical_request_id=request.logical_request_id,
        sequence_number=1,
        lease_generation=1,
        created_at="2026-09-21T00:00:01+00:00",
    )
    repository.add_physical_attempt(first)

    with pytest.raises(RuntimeStorageError, match="physical_attempt_persistence_failed"):
        repository.add_physical_attempt(first.model_copy(update={"physical_attempt_id": "attempt-2"}))


def test_repository_allocates_contiguous_attempt_sequences_transactionally(tmp_path: Path) -> None:
    """Allocate sequence numbers under the current fence instead of trusting callers."""
    root = _runtime_root(tmp_path)
    repository = initialize_runtime_storage(root)
    repository.add_logical_request(_logical_request())
    now = datetime(2026, 9, 21, tzinfo=UTC)
    lease = repository.acquire_lease("owner-a", now, RuntimeLeasePolicy())

    first = repository.reserve_next_physical_attempt("attempt-1", "logical-1", lease, now)
    with sqlite3.connect(root / DATABASE_FILENAME) as connection:
        connection.execute("UPDATE physical_attempts SET state = 'failed' WHERE physical_attempt_id = 'attempt-1'")
    second = repository.reserve_next_physical_attempt("attempt-2", "logical-1", lease, now + timedelta(seconds=1))

    assert (first.sequence_number, second.sequence_number) == (1, 2)
    with pytest.raises(RuntimeStorageError, match="physical_attempt_already_active"):
        repository.reserve_next_physical_attempt("attempt-3", "logical-1", lease, now + timedelta(seconds=2))


def test_repository_discards_only_never_gated_reservation(tmp_path: Path) -> None:
    """Let a blocked pre-send path remove its reservation without touching gated attempts."""
    repository = initialize_runtime_storage(_runtime_root(tmp_path))
    repository.add_logical_request(_logical_request())
    now = datetime(2026, 9, 21, tzinfo=UTC)
    lease = repository.acquire_lease("owner-a", now, RuntimeLeasePolicy())
    repository.reserve_next_physical_attempt("attempt-1", "logical-1", lease, now)

    repository.discard_reserved_attempt("attempt-1", lease, now + timedelta(seconds=1))

    assert repository.physical_attempt_state("attempt-1") is None
    replacement = repository.reserve_next_physical_attempt("attempt-2", "logical-1", lease, now + timedelta(seconds=2))
    assert replacement.sequence_number == 1


def test_repository_completes_logical_request_atomically(tmp_path: Path) -> None:
    """Persist the sanitized result and terminal state in one transaction."""
    repository = initialize_runtime_storage(_runtime_root(tmp_path))
    repository.add_logical_request(_logical_request())
    now = datetime(2026, 9, 21, tzinfo=UTC)
    lease = repository.acquire_lease("owner-a", now, RuntimeLeasePolicy())
    result = ProductionLogicalResult(
        logical_request_id="logical-1",
        outcome=LogicalResultOutcome.SUCCEEDED,
        completed_at="2026-09-21T00:00:02+00:00",
        error_code=None,
    )

    repository.complete_logical_request(result, lease, now)

    assert repository.logical_request_state("logical-1") == "succeeded"


def test_repository_asserts_claimed_logical_request_lineage(tmp_path: Path) -> None:
    """Verify the complete persisted request and claimed queue identity before a send."""
    repository = initialize_runtime_storage(_runtime_root(tmp_path))
    now = datetime(2026, 9, 21, tzinfo=UTC)
    lease = repository.acquire_lease("owner-a", now, RuntimeLeasePolicy())
    repository.enqueue_logical_request(_logical_request(), "provider-a", lease, now, QueuePolicy())
    assert repository.claim_next_queued("provider-a", lease, now + timedelta(seconds=1)) is not None

    _assert_lineage(repository, lease, now + timedelta(seconds=2), _expected_lineage())


@pytest.mark.parametrize(
    ("field", "unexpected"),
    [
        ("task_id", "task-2"),
        ("source_id", "other-source"),
        ("operation", "other-operation"),
        ("source_approval_version", 2),
        ("source_profile_version", 2),
        ("credential_scope_alias", "other-credential"),
        ("egress_scope", "other-egress"),
        ("rate_domain", "other-provider"),
    ],
)
def test_repository_rejects_claimed_logical_request_lineage_mismatch(
    tmp_path: Path, field: str, unexpected: object
) -> None:
    """Fail closed when any policy-derived lineage value differs from durable state."""
    repository = initialize_runtime_storage(_runtime_root(tmp_path))
    now = datetime(2026, 9, 21, tzinfo=UTC)
    lease = repository.acquire_lease("owner-a", now, RuntimeLeasePolicy())
    repository.enqueue_logical_request(_logical_request(), "provider-a", lease, now, QueuePolicy())
    assert repository.claim_next_queued("provider-a", lease, now + timedelta(seconds=1)) is not None
    lineage = _expected_lineage()
    lineage[field] = unexpected

    with pytest.raises(RuntimeStorageError, match="logical_request_lineage_mismatch"):
        _assert_lineage(repository, lease, now + timedelta(seconds=2), lineage)


def test_repository_requires_claimed_queue_and_current_fence_for_lineage(tmp_path: Path) -> None:
    """Reject an unclaimed request and a stale owner before transport composition."""
    repository = initialize_runtime_storage(_runtime_root(tmp_path))
    now = datetime(2026, 9, 21, tzinfo=UTC)
    lease = repository.acquire_lease("owner-a", now, RuntimeLeasePolicy())
    repository.enqueue_logical_request(_logical_request(), "provider-a", lease, now, QueuePolicy())

    with pytest.raises(RuntimeStorageError, match="logical_request_lineage_mismatch"):
        _assert_lineage(repository, lease, now + timedelta(seconds=1), _expected_lineage())
    repository.release_lease(lease, now + timedelta(seconds=2))
    with pytest.raises(RuntimeStorageError, match="stale_runtime_lease"):
        _assert_lineage(repository, lease, now + timedelta(seconds=3), _expected_lineage())


def test_strict_finalization_rejects_active_attempt_and_second_result(tmp_path: Path) -> None:
    """Require terminal attempts and persist exactly one logical result."""
    root = _runtime_root(tmp_path)
    repository = initialize_runtime_storage(root)
    repository.add_logical_request(_logical_request())
    now = datetime(2026, 9, 21, tzinfo=UTC)
    lease = repository.acquire_lease("owner-a", now, RuntimeLeasePolicy())
    repository.reserve_next_physical_attempt("attempt-1", "logical-1", lease, now)
    success = ProductionLogicalResult(
        logical_request_id="logical-1",
        outcome=LogicalResultOutcome.SUCCEEDED,
        completed_at=(now + timedelta(seconds=2)).isoformat(),
        error_code=None,
    )

    with pytest.raises(RuntimeStorageError, match="logical_request_has_active_attempt"):
        repository.finalize_logical_request(success, lease, now + timedelta(seconds=1))
    database = root / DATABASE_FILENAME
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE physical_attempts SET state = 'succeeded' WHERE physical_attempt_id = 'attempt-1'")
    repository.finalize_logical_request(success, lease, now + timedelta(seconds=2))

    with pytest.raises(RuntimeStorageError, match="logical_request_not_active"):
        repository.finalize_logical_request(success, lease, now + timedelta(seconds=3))


def test_repository_rolls_back_state_when_result_insert_fails(tmp_path: Path) -> None:
    """Leave the request queued when result persistence fails after its update."""
    root = _runtime_root(tmp_path)
    repository = initialize_runtime_storage(root)
    repository.add_logical_request(_logical_request())
    now = datetime(2026, 9, 21, tzinfo=UTC)
    lease = repository.acquire_lease("owner-a", now, RuntimeLeasePolicy())
    with sqlite3.connect(root / DATABASE_FILENAME) as connection:
        connection.execute(
            """
            CREATE TRIGGER reject_logical_result
            BEFORE INSERT ON logical_results
            BEGIN
                SELECT RAISE(ABORT, 'fixture rejection');
            END
            """
        )
    result = ProductionLogicalResult(
        logical_request_id="logical-1",
        outcome=LogicalResultOutcome.FAILED,
        completed_at="2026-09-21T00:00:02+00:00",
        error_code="fixture_failure",
    )

    with pytest.raises(RuntimeStorageError, match="logical_result_persistence_failed"):
        repository.complete_logical_request(result, lease, now)

    assert repository.logical_request_state("logical-1") == "queued"


def test_new_attempt_must_start_reserved() -> None:
    """Forbid callers from inventing a later initial attempt state."""
    with pytest.raises(ValidationError, match="new physical attempts must be reserved"):
        ProductionPhysicalAttempt(
            physical_attempt_id="attempt-1",
            logical_request_id="logical-1",
            sequence_number=1,
            lease_generation=1,
            created_at="2026-09-21T00:00:01+00:00",
            state=PhysicalAttemptState.STARTED,
        )


def test_existing_database_must_remain_private(tmp_path: Path) -> None:
    """Refuse a database whose permissions became broader after creation."""
    root = _runtime_root(tmp_path)
    initialize_runtime_storage(root)
    database = root / DATABASE_FILENAME
    database.chmod(0o644)

    with pytest.raises(RuntimeStorageError, match="invalid_runtime_database_permissions"):
        initialize_runtime_storage(root)


def test_runtime_database_does_not_depend_on_process_umask(tmp_path: Path) -> None:
    """Force private database permissions even under a permissive umask."""
    root = _runtime_root(tmp_path)
    previous = os.umask(0)
    try:
        initialize_runtime_storage(root)
    finally:
        os.umask(previous)

    assert (root / DATABASE_FILENAME).stat().st_mode & 0o777 == 0o600
