import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from stock_research_llm_orchestrator.requests.production import (
    LogicalResultOutcome,
    PhysicalAttemptState,
    ProductionLogicalRequest,
    ProductionLogicalResult,
    ProductionPhysicalAttempt,
    RuntimeLeasePolicy,
)
from stock_research_llm_orchestrator.requests.storage import (
    DATABASE_FILENAME,
    RuntimeStorageError,
    initialize_runtime_storage,
)


def _runtime_root(tmp_path: Path) -> Path:
    root = tmp_path / ".runtime"
    root.mkdir(mode=0o700)
    return root


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


def test_initialize_runtime_storage_creates_versioned_private_database(tmp_path: Path) -> None:
    """Create the exact current schema with owner-only database permissions."""
    root = _runtime_root(tmp_path)

    initialize_runtime_storage(root)

    database = root / DATABASE_FILENAME
    assert database.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (3,)
        assert connection.execute("SELECT schema_owner, schema_version FROM schema_metadata").fetchone() == (
            "production-request-coordinator",
            3,
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
        connection.execute("DROP TABLE queue_events")
        connection.execute("DROP TABLE provider_queue_cursors")
        connection.execute("DROP TABLE queue_entries")
        connection.execute("ALTER TABLE runtime_lease DROP COLUMN active")
        connection.execute("UPDATE schema_metadata SET schema_version = 1")
        connection.execute("PRAGMA user_version = 1")

    migrated = initialize_runtime_storage(root)

    assert migrated.logical_request_state("logical-1") == "queued"
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (3,)
        columns = {row[1] for row in connection.execute("PRAGMA table_info(runtime_lease)")}
    assert "active" in columns


def test_initialize_runtime_storage_migrates_phase_3b_schema(tmp_path: Path) -> None:
    """Add durable queue state to an existing Phase 3B database."""
    root = _runtime_root(tmp_path)
    repository = initialize_runtime_storage(root)
    repository.add_logical_request(_logical_request())
    database = root / DATABASE_FILENAME
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE queue_events")
        connection.execute("DROP TABLE provider_queue_cursors")
        connection.execute("DROP TABLE queue_entries")
        connection.execute("UPDATE schema_metadata SET schema_version = 2")
        connection.execute("PRAGMA user_version = 2")

    migrated = initialize_runtime_storage(root)

    assert migrated.logical_request_state("logical-1") == "queued"
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone() == (3,)
        tables = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'queue_%'")
        }
    assert tables == {"queue_entries", "queue_events"}


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
