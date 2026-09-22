"""Versioned SQLite storage for the production request runtime."""

import os
import sqlite3
import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import TypeAdapter

from stock_research_llm_orchestrator.contracts.base import Identifier
from stock_research_llm_orchestrator.requests.production import (
    LogicalResultOutcome,
    ProductionLogicalRequest,
    ProductionLogicalResult,
    ProductionPhysicalAttempt,
    QueueClaim,
    QueuePolicy,
    RuntimeLease,
    RuntimeLeasePolicy,
)


SCHEMA_VERSION = 3
DATABASE_FILENAME = "request-coordinator.sqlite3"
_SCHEMA_OWNER = "production-request-coordinator"
_OWNER_TOKEN_ADAPTER = TypeAdapter(Identifier)


class RuntimeStorageError(RuntimeError):
    """Sanitized fail-closed storage error."""


class ProductionRequestRepository:
    """Persist production request intent before any external send."""

    def __init__(self, database_path: Path) -> None:
        """Bind to an initialized runtime database."""
        self._database_path = database_path

    def add_logical_request(self, request: ProductionLogicalRequest) -> None:
        """Insert one immutable logical request transactionally."""
        request = ProductionLogicalRequest.model_validate(request.model_dump(warnings=False))
        try:
            with _connect(self._database_path) as connection:
                _verify_schema(connection)
                connection.execute(
                    """
                    INSERT INTO logical_requests (
                        logical_request_id, task_id, source_id, operation,
                        request_fingerprint, source_approval_version,
                        source_profile_version, credential_scope_alias,
                        egress_scope, created_at, state
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        request.logical_request_id,
                        request.task_id,
                        request.source_id,
                        request.operation,
                        request.request_fingerprint,
                        request.source_approval_version,
                        request.source_profile_version,
                        request.credential_scope_alias,
                        request.egress_scope,
                        request.created_at,
                        request.state.value,
                    ),
                )
        except (sqlite3.Error, OSError) as error:
            raise RuntimeStorageError("logical_request_persistence_failed") from error

    def enqueue_logical_request(
        self,
        request: ProductionLogicalRequest,
        rate_domain: str,
        lease: RuntimeLease,
        now: datetime,
        policy: QueuePolicy,
    ) -> None:
        """Persist one request, enforce bounds, and append queue lifecycle events."""
        request = ProductionLogicalRequest.model_validate(request.model_dump(warnings=False))
        rate_domain = _OWNER_TOKEN_ADAPTER.validate_python(rate_domain, strict=True)
        lease = RuntimeLease.model_validate(lease.model_dump(warnings=False))
        now = _validate_utc(now)
        policy = QueuePolicy.model_validate(policy.model_dump(warnings=False))
        rejected = False
        try:
            with _connect(self._database_path) as connection:
                _verify_schema(connection)
                connection.execute("BEGIN IMMEDIATE")
                _assert_fence(connection, lease, now)
                _insert_logical_request(connection, request)
                _append_queue_event(connection, request.logical_request_id, "requested", now, None)
                counts = connection.execute(
                    """
                    SELECT
                        COUNT(*),
                        SUM(CASE WHEN rate_domain = ? THEN 1 ELSE 0 END),
                        SUM(CASE WHEN rate_domain = ? AND task_id = ? THEN 1 ELSE 0 END)
                    FROM queue_entries WHERE state = 'queued'
                    """,
                    (rate_domain, rate_domain, request.task_id),
                ).fetchone()
                global_count, domain_count, task_count = (int(value or 0) for value in counts)
                rejected = (
                    global_count >= policy.global_limit
                    or domain_count >= policy.rate_domain_limit
                    or task_count >= policy.task_rate_domain_limit
                )
                state = "rejected" if rejected else "queued"
                connection.execute(
                    """
                    INSERT INTO queue_entries (
                        logical_request_id, rate_domain, task_id, enqueued_at, state, dequeued_at
                    ) VALUES (?, ?, ?, ?, ?, NULL)
                    """,
                    (request.logical_request_id, rate_domain, request.task_id, _timestamp(now), state),
                )
                if rejected:
                    connection.execute(
                        "UPDATE logical_requests SET state = 'failed' WHERE logical_request_id = ?",
                        (request.logical_request_id,),
                    )
                    connection.execute(
                        """
                        INSERT INTO logical_results (logical_request_id, outcome, completed_at, error_code)
                        VALUES (?, 'failed', ?, 'queue_full')
                        """,
                        (request.logical_request_id, _timestamp(now)),
                    )
                    _append_queue_event(connection, request.logical_request_id, "rejected", now, "queue_full")
                else:
                    _append_queue_event(connection, request.logical_request_id, "queued", now, None)
        except RuntimeStorageError:
            raise
        except (sqlite3.Error, OSError) as error:
            raise RuntimeStorageError("queue_enqueue_failed") from error
        if rejected:
            raise RuntimeStorageError("queue_full")

    def claim_next_queued(self, rate_domain: str, lease: RuntimeLease, now: datetime) -> QueueClaim | None:
        """Dequeue one request using task FIFO and active-task round-robin."""
        rate_domain = _OWNER_TOKEN_ADAPTER.validate_python(rate_domain, strict=True)
        lease = RuntimeLease.model_validate(lease.model_dump(warnings=False))
        now = _validate_utc(now)
        try:
            with _connect(self._database_path) as connection:
                _verify_schema(connection)
                connection.execute("BEGIN IMMEDIATE")
                _assert_fence(connection, lease, now)
                tasks = [
                    str(row[0])
                    for row in connection.execute(
                        """
                        SELECT task_id FROM queue_entries
                        WHERE rate_domain = ? AND state = 'queued'
                        GROUP BY task_id ORDER BY task_id
                        """,
                        (rate_domain,),
                    )
                ]
                if not tasks:
                    return None
                cursor = connection.execute(
                    "SELECT last_task_id FROM provider_queue_cursors WHERE rate_domain = ?",
                    (rate_domain,),
                ).fetchone()
                last_task = None if cursor is None else str(cursor[0])
                task_id = next(
                    (candidate for candidate in tasks if last_task is None or candidate > last_task), tasks[0]
                )
                row = connection.execute(
                    """
                    SELECT logical_request_id, enqueued_at FROM queue_entries
                    WHERE rate_domain = ? AND task_id = ? AND state = 'queued'
                    ORDER BY enqueued_at, logical_request_id LIMIT 1
                    """,
                    (rate_domain, task_id),
                ).fetchone()
                if row is None:
                    raise RuntimeStorageError("queue_scheduler_inconsistent")
                logical_request_id, enqueued_at = (str(value) for value in row)
                updated = connection.execute(
                    """
                    UPDATE queue_entries SET state = 'dequeued', dequeued_at = ?
                    WHERE logical_request_id = ? AND state = 'queued'
                    """,
                    (_timestamp(now), logical_request_id),
                )
                if updated.rowcount != 1:
                    raise RuntimeStorageError("queue_claim_race")
                connection.execute(
                    """
                    INSERT INTO provider_queue_cursors (rate_domain, last_task_id)
                    VALUES (?, ?) ON CONFLICT(rate_domain) DO UPDATE SET last_task_id = excluded.last_task_id
                    """,
                    (rate_domain, task_id),
                )
                _append_queue_event(connection, logical_request_id, "dequeued", now, None)
        except RuntimeStorageError:
            raise
        except (sqlite3.Error, OSError) as error:
            raise RuntimeStorageError("queue_claim_failed") from error
        return QueueClaim(
            logical_request_id=logical_request_id,
            task_id=task_id,
            rate_domain=rate_domain,
            enqueued_at=enqueued_at,
        )

    def queue_events(self, logical_request_id: str) -> tuple[tuple[str, str | None], ...]:
        """Return sanitized lifecycle events for one logical request."""
        try:
            with _connect(self._database_path) as connection:
                _verify_schema(connection)
                rows = connection.execute(
                    """
                    SELECT event_type, reason FROM queue_events
                    WHERE logical_request_id = ? ORDER BY event_id
                    """,
                    (logical_request_id,),
                ).fetchall()
        except (sqlite3.Error, OSError) as error:
            raise RuntimeStorageError("queue_event_read_failed") from error
        return tuple((str(event), None if reason is None else str(reason)) for event, reason in rows)

    def add_physical_attempt(self, attempt: ProductionPhysicalAttempt) -> None:
        """Reserve one attempt after its logical request is durable."""
        attempt = ProductionPhysicalAttempt.model_validate(attempt.model_dump(warnings=False))
        try:
            with _connect(self._database_path) as connection:
                _verify_schema(connection)
                connection.execute(
                    """
                    INSERT INTO physical_attempts (
                        physical_attempt_id, logical_request_id, sequence_number,
                        lease_generation, created_at, state
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        attempt.physical_attempt_id,
                        attempt.logical_request_id,
                        attempt.sequence_number,
                        attempt.lease_generation,
                        attempt.created_at,
                        attempt.state.value,
                    ),
                )
        except (sqlite3.Error, OSError) as error:
            raise RuntimeStorageError("physical_attempt_persistence_failed") from error

    def logical_request_state(self, logical_request_id: str) -> str | None:
        """Return the persisted state without exposing request contents."""
        try:
            with _connect(self._database_path) as connection:
                _verify_schema(connection)
                row = connection.execute(
                    "SELECT state FROM logical_requests WHERE logical_request_id = ?", (logical_request_id,)
                ).fetchone()
        except (sqlite3.Error, OSError) as error:
            raise RuntimeStorageError("logical_request_read_failed") from error
        return None if row is None else str(row[0])

    def physical_attempt_state(self, physical_attempt_id: str) -> tuple[str, int] | None:
        """Return a physical attempt state and fencing generation."""
        try:
            with _connect(self._database_path) as connection:
                _verify_schema(connection)
                row = connection.execute(
                    "SELECT state, lease_generation FROM physical_attempts WHERE physical_attempt_id = ?",
                    (physical_attempt_id,),
                ).fetchone()
        except (sqlite3.Error, OSError) as error:
            raise RuntimeStorageError("physical_attempt_read_failed") from error
        return None if row is None else (str(row[0]), int(row[1]))

    def mark_physical_attempt_started(self, physical_attempt_id: str, lease: RuntimeLease, now: datetime) -> None:
        """Fence the durable transition immediately before external send."""
        physical_attempt_id = _OWNER_TOKEN_ADAPTER.validate_python(physical_attempt_id, strict=True)
        lease = RuntimeLease.model_validate(lease.model_dump(warnings=False))
        now = _validate_utc(now)
        try:
            with _connect(self._database_path) as connection:
                _verify_schema(connection)
                connection.execute("BEGIN IMMEDIATE")
                _assert_fence(connection, lease, now)
                updated = connection.execute(
                    """
                    UPDATE physical_attempts SET state = 'started'
                    WHERE physical_attempt_id = ? AND lease_generation = ? AND state = 'reserved'
                    """,
                    (physical_attempt_id, lease.generation),
                )
                if updated.rowcount != 1:
                    raise RuntimeStorageError("physical_attempt_transition_rejected")
        except RuntimeStorageError:
            raise
        except (sqlite3.Error, OSError) as error:
            raise RuntimeStorageError("physical_attempt_transition_failed") from error

    def complete_logical_request(self, result: ProductionLogicalResult, lease: RuntimeLease, now: datetime) -> None:
        """Atomically fence and persist one terminal logical result."""
        result = ProductionLogicalResult.model_validate(result.model_dump(warnings=False))
        lease = RuntimeLease.model_validate(lease.model_dump(warnings=False))
        now = _validate_utc(now)
        state = {
            LogicalResultOutcome.SUCCEEDED: "succeeded",
            LogicalResultOutcome.FAILED: "failed",
            LogicalResultOutcome.CANCELLED: "cancelled",
            LogicalResultOutcome.UNKNOWN: "failed",
        }[result.outcome]
        try:
            with _connect(self._database_path) as connection:
                _verify_schema(connection)
                connection.execute("BEGIN IMMEDIATE")
                _assert_fence(connection, lease, now)
                updated = connection.execute(
                    "UPDATE logical_requests SET state = ? WHERE logical_request_id = ? AND state = 'queued'",
                    (state, result.logical_request_id),
                )
                if updated.rowcount != 1:
                    raise sqlite3.IntegrityError("logical request is missing or terminal")
                connection.execute(
                    """
                    INSERT INTO logical_results (logical_request_id, outcome, completed_at, error_code)
                    VALUES (?, ?, ?, ?)
                    """,
                    (result.logical_request_id, result.outcome.value, result.completed_at, result.error_code),
                )
        except (sqlite3.Error, OSError) as error:
            raise RuntimeStorageError("logical_result_persistence_failed") from error

    def acquire_lease(self, owner_token: str, now: datetime, policy: RuntimeLeasePolicy) -> RuntimeLease:
        """Acquire an unowned lease while serializing competing owners."""
        owner_token = _OWNER_TOKEN_ADAPTER.validate_python(owner_token, strict=True)
        now = _validate_utc(now)
        policy = RuntimeLeasePolicy.model_validate(policy.model_dump(warnings=False))
        expires = now + timedelta(seconds=policy.lease_duration_seconds)
        try:
            with _connect(self._database_path) as connection:
                _verify_schema(connection)
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    """
                    SELECT owner_token, generation, heartbeat_at, expires_at, active
                    FROM runtime_lease WHERE singleton = 1
                    """
                ).fetchone()
                if row is None:
                    generation = 1
                    connection.execute(
                        "INSERT INTO runtime_lease VALUES (1, ?, ?, ?, ?, ?, 1)",
                        (owner_token, generation, _timestamp(now), _timestamp(now), _timestamp(expires)),
                    )
                else:
                    _, previous_generation, heartbeat_at, expires_at, active = row
                    if int(active) == 1:
                        if now < _parse_timestamp(str(heartbeat_at)):
                            raise RuntimeStorageError("runtime_clock_rollback")
                        if now < _parse_timestamp(str(expires_at)):
                            raise RuntimeStorageError("runtime_lease_held")
                        raise RuntimeStorageError("lease_reconciliation_required")
                    generation = int(previous_generation) + 1
                    connection.execute(
                        """
                        UPDATE runtime_lease
                        SET owner_token = ?, generation = ?, acquired_at = ?,
                            heartbeat_at = ?, expires_at = ?, active = 1
                        WHERE singleton = 1 AND active = 0
                        """,
                        (owner_token, generation, _timestamp(now), _timestamp(now), _timestamp(expires)),
                    )
        except RuntimeStorageError:
            raise
        except (sqlite3.Error, OSError, TypeError, ValueError) as error:
            raise RuntimeStorageError("runtime_lease_acquisition_failed") from error
        return RuntimeLease(
            owner_token=owner_token,
            generation=generation,
            acquired_at=_timestamp(now),
            heartbeat_at=_timestamp(now),
            expires_at=_timestamp(expires),
        )

    def recover_expired_lease(self, owner_token: str, now: datetime, policy: RuntimeLeasePolicy) -> RuntimeLease:
        """Reconcile interrupted attempts and atomically take over an expired lease."""
        owner_token = _OWNER_TOKEN_ADAPTER.validate_python(owner_token, strict=True)
        now = _validate_utc(now)
        policy = RuntimeLeasePolicy.model_validate(policy.model_dump(warnings=False))
        expires = now + timedelta(seconds=policy.lease_duration_seconds)
        try:
            with _connect(self._database_path) as connection:
                _verify_schema(connection)
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    """
                    SELECT generation, heartbeat_at, expires_at, active
                    FROM runtime_lease WHERE singleton = 1
                    """
                ).fetchone()
                if row is None or int(row[3]) != 1:
                    raise RuntimeStorageError("expired_runtime_lease_not_found")
                previous_generation = int(row[0])
                heartbeat = _parse_timestamp(str(row[1]))
                expiry = _parse_timestamp(str(row[2]))
                if now < heartbeat:
                    raise RuntimeStorageError("runtime_clock_rollback")
                if now < expiry:
                    raise RuntimeStorageError("runtime_lease_held")
                _validate_recovery_state(connection, previous_generation)
                generation = previous_generation + 1
                connection.execute(
                    """
                    INSERT INTO queue_events (logical_request_id, event_type, occurred_at, reason)
                    SELECT entry.logical_request_id, 'queued', ?, 'owner_interrupted'
                    FROM queue_entries AS entry
                    WHERE entry.state = 'dequeued'
                      AND NOT EXISTS (
                          SELECT 1 FROM physical_attempts AS attempt
                          WHERE attempt.logical_request_id = entry.logical_request_id
                      )
                    """,
                    (_timestamp(now),),
                )
                connection.execute(
                    """
                    UPDATE queue_entries SET state = 'queued', dequeued_at = NULL
                    WHERE state = 'dequeued'
                      AND NOT EXISTS (
                          SELECT 1 FROM physical_attempts
                          WHERE physical_attempts.logical_request_id = queue_entries.logical_request_id
                      )
                    """
                )
                started_requests = connection.execute(
                    """
                    SELECT DISTINCT logical_request_id FROM physical_attempts
                    WHERE state = 'started' AND lease_generation <= ?
                    """,
                    (previous_generation,),
                ).fetchall()
                for (logical_request_id,) in started_requests:
                    updated = connection.execute(
                        """
                        UPDATE logical_requests SET state = 'failed'
                        WHERE logical_request_id = ? AND state = 'queued'
                        """,
                        (logical_request_id,),
                    )
                    if updated.rowcount != 1:
                        raise RuntimeStorageError("runtime_recovery_audit_gap")
                    connection.execute(
                        """
                        INSERT INTO logical_results (logical_request_id, outcome, completed_at, error_code)
                        VALUES (?, 'unknown', ?, 'owner_interrupted')
                        """,
                        (logical_request_id, _timestamp(now)),
                    )
                connection.execute(
                    """
                    UPDATE physical_attempts SET state = 'unknown'
                    WHERE state = 'started' AND lease_generation <= ?
                    """,
                    (previous_generation,),
                )
                connection.execute(
                    """
                    UPDATE physical_attempts SET lease_generation = ?
                    WHERE state = 'reserved' AND lease_generation <= ?
                    """,
                    (generation, previous_generation),
                )
                lease_updated = connection.execute(
                    """
                    UPDATE runtime_lease
                    SET owner_token = ?, generation = ?, acquired_at = ?,
                        heartbeat_at = ?, expires_at = ?, active = 1
                    WHERE singleton = 1 AND generation = ? AND active = 1
                    """,
                    (
                        owner_token,
                        generation,
                        _timestamp(now),
                        _timestamp(now),
                        _timestamp(expires),
                        previous_generation,
                    ),
                )
                if lease_updated.rowcount != 1:
                    raise RuntimeStorageError("runtime_recovery_lease_race")
        except RuntimeStorageError:
            raise
        except (sqlite3.Error, OSError, TypeError, ValueError) as error:
            raise RuntimeStorageError("runtime_recovery_failed") from error
        return RuntimeLease(
            owner_token=owner_token,
            generation=generation,
            acquired_at=_timestamp(now),
            heartbeat_at=_timestamp(now),
            expires_at=_timestamp(expires),
        )

    def heartbeat_lease(self, lease: RuntimeLease, now: datetime, policy: RuntimeLeasePolicy) -> RuntimeLease:
        """Extend an active lease only for its current fencing generation."""
        lease = RuntimeLease.model_validate(lease.model_dump(warnings=False))
        now = _validate_utc(now)
        policy = RuntimeLeasePolicy.model_validate(policy.model_dump(warnings=False))
        heartbeat = _parse_timestamp(lease.heartbeat_at)
        expiry = _parse_timestamp(lease.expires_at)
        if now < heartbeat:
            raise RuntimeStorageError("runtime_clock_rollback")
        if now >= expiry:
            raise RuntimeStorageError("runtime_lease_expired")
        expires = now + timedelta(seconds=policy.lease_duration_seconds)
        try:
            with _connect(self._database_path) as connection:
                _verify_schema(connection)
                updated = connection.execute(
                    """
                    UPDATE runtime_lease SET heartbeat_at = ?, expires_at = ?
                    WHERE singleton = 1 AND owner_token = ? AND generation = ? AND active = 1
                    """,
                    (_timestamp(now), _timestamp(expires), lease.owner_token, lease.generation),
                )
                if updated.rowcount != 1:
                    raise RuntimeStorageError("stale_runtime_lease")
        except RuntimeStorageError:
            raise
        except (sqlite3.Error, OSError) as error:
            raise RuntimeStorageError("runtime_lease_heartbeat_failed") from error
        return lease.model_copy(update={"heartbeat_at": _timestamp(now), "expires_at": _timestamp(expires)})

    def release_lease(self, lease: RuntimeLease, now: datetime) -> None:
        """Release only the current unexpired lease generation."""
        lease = RuntimeLease.model_validate(lease.model_dump(warnings=False))
        now = _validate_utc(now)
        if now < _parse_timestamp(lease.heartbeat_at):
            raise RuntimeStorageError("runtime_clock_rollback")
        if now >= _parse_timestamp(lease.expires_at):
            raise RuntimeStorageError("runtime_lease_expired")
        try:
            with _connect(self._database_path) as connection:
                _verify_schema(connection)
                updated = connection.execute(
                    """
                    UPDATE runtime_lease SET active = 0
                    WHERE singleton = 1 AND owner_token = ? AND generation = ? AND active = 1
                    """,
                    (lease.owner_token, lease.generation),
                )
                if updated.rowcount != 1:
                    raise RuntimeStorageError("stale_runtime_lease")
        except RuntimeStorageError:
            raise
        except (sqlite3.Error, OSError) as error:
            raise RuntimeStorageError("runtime_lease_release_failed") from error

    def validate_fence(self, lease: RuntimeLease, now: datetime) -> None:
        """Fail closed before a mutation or future transport permit."""
        lease = RuntimeLease.model_validate(lease.model_dump(warnings=False))
        now = _validate_utc(now)
        try:
            with _connect(self._database_path) as connection:
                _verify_schema(connection)
                _assert_fence(connection, lease, now)
        except RuntimeStorageError:
            raise
        except (sqlite3.Error, OSError) as error:
            raise RuntimeStorageError("runtime_fence_validation_failed") from error


def _insert_logical_request(connection: sqlite3.Connection, request: ProductionLogicalRequest) -> None:
    connection.execute(
        """
        INSERT INTO logical_requests (
            logical_request_id, task_id, source_id, operation,
            request_fingerprint, source_approval_version,
            source_profile_version, credential_scope_alias,
            egress_scope, created_at, state
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            request.logical_request_id,
            request.task_id,
            request.source_id,
            request.operation,
            request.request_fingerprint,
            request.source_approval_version,
            request.source_profile_version,
            request.credential_scope_alias,
            request.egress_scope,
            request.created_at,
            request.state.value,
        ),
    )


def _append_queue_event(
    connection: sqlite3.Connection,
    logical_request_id: str,
    event_type: str,
    now: datetime,
    reason: str | None,
) -> None:
    connection.execute(
        """
        INSERT INTO queue_events (logical_request_id, event_type, occurred_at, reason)
        VALUES (?, ?, ?, ?)
        """,
        (logical_request_id, event_type, _timestamp(now), reason),
    )


def _assert_fence(connection: sqlite3.Connection, lease: RuntimeLease, now: datetime) -> None:
    row = connection.execute(
        """
        SELECT heartbeat_at, expires_at FROM runtime_lease
        WHERE singleton = 1 AND owner_token = ? AND generation = ? AND active = 1
        """,
        (lease.owner_token, lease.generation),
    ).fetchone()
    if row is None:
        raise RuntimeStorageError("stale_runtime_lease")
    heartbeat, expires = (_parse_timestamp(str(value)) for value in row)
    if now < heartbeat:
        raise RuntimeStorageError("runtime_clock_rollback")
    if now >= expires:
        raise RuntimeStorageError("runtime_lease_expired")


def _validate_recovery_state(connection: sqlite3.Connection, previous_generation: int) -> None:
    future_attempt = connection.execute(
        "SELECT 1 FROM physical_attempts WHERE lease_generation > ? LIMIT 1",
        (previous_generation,),
    ).fetchone()
    if future_attempt is not None:
        raise RuntimeStorageError("runtime_recovery_clock_or_generation_anomaly")
    audit_gap = connection.execute(
        """
        SELECT 1
        FROM physical_attempts AS attempt
        JOIN logical_requests AS request USING (logical_request_id)
        LEFT JOIN logical_results AS result USING (logical_request_id)
        WHERE (
            attempt.state = 'started'
            AND (request.state != 'queued' OR result.logical_request_id IS NOT NULL)
        ) OR (
            attempt.state IN ('succeeded', 'failed', 'unknown')
            AND (request.state = 'queued' OR result.logical_request_id IS NULL)
        )
        LIMIT 1
        """
    ).fetchone()
    if audit_gap is not None:
        raise RuntimeStorageError("runtime_recovery_audit_gap")


def initialize_runtime_storage(runtime_root: Path) -> ProductionRequestRepository:
    """Validate a trusted runtime root and initialize the current schema."""
    _validate_runtime_root(runtime_root)
    database_path = runtime_root / DATABASE_FILENAME
    existed = database_path.exists() or database_path.is_symlink()
    if database_path.is_symlink() or (existed and not database_path.is_file()):
        raise RuntimeStorageError("invalid_runtime_database")
    try:
        connection = _connect(database_path)
        try:
            if not existed:
                os.chmod(database_path, 0o600)
            _validate_database_mode(database_path)
            with connection:
                current_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                if current_version == 0:
                    _create_schema(connection)
                elif current_version > SCHEMA_VERSION:
                    raise RuntimeStorageError("unsupported_runtime_schema")
                else:
                    if current_version == 1:
                        _migrate_v1_to_v2(connection)
                        current_version = 2
                    if current_version == 2:
                        _migrate_v2_to_v3(connection)
                _verify_schema(connection)
        finally:
            connection.close()
        return ProductionRequestRepository(database_path)
    except RuntimeStorageError:
        raise
    except (sqlite3.Error, OSError, TypeError, ValueError) as error:
        raise RuntimeStorageError("runtime_storage_initialization_failed") from error


def _connect(database_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path, timeout=5, isolation_level="DEFERRED")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = DELETE")
    connection.execute("PRAGMA synchronous = FULL")
    return connection


def _create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        BEGIN IMMEDIATE;

        CREATE TABLE schema_metadata (
            singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
            schema_owner TEXT NOT NULL,
            schema_version INTEGER NOT NULL CHECK (schema_version >= 1)
        );
        INSERT INTO schema_metadata VALUES (1, 'production-request-coordinator', 3);

        CREATE TABLE logical_requests (
            logical_request_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            operation TEXT NOT NULL,
            request_fingerprint TEXT NOT NULL,
            source_approval_version INTEGER NOT NULL CHECK (source_approval_version >= 1),
            source_profile_version INTEGER NOT NULL CHECK (source_profile_version >= 1),
            credential_scope_alias TEXT,
            egress_scope TEXT NOT NULL,
            created_at TEXT NOT NULL,
            state TEXT NOT NULL CHECK (state IN ('queued', 'cancelled', 'succeeded', 'failed'))
        );

        CREATE TABLE physical_attempts (
            physical_attempt_id TEXT PRIMARY KEY,
            logical_request_id TEXT NOT NULL REFERENCES logical_requests(logical_request_id),
            sequence_number INTEGER NOT NULL CHECK (sequence_number >= 1),
            lease_generation INTEGER NOT NULL CHECK (lease_generation >= 1),
            created_at TEXT NOT NULL,
            state TEXT NOT NULL CHECK (state IN ('reserved', 'started', 'succeeded', 'failed', 'unknown')),
            UNIQUE (logical_request_id, sequence_number)
        );

        CREATE TABLE logical_results (
            logical_request_id TEXT PRIMARY KEY REFERENCES logical_requests(logical_request_id),
            outcome TEXT NOT NULL CHECK (outcome IN ('succeeded', 'failed', 'cancelled', 'unknown')),
            completed_at TEXT NOT NULL,
            error_code TEXT
        );

        CREATE TABLE runtime_lease (
            singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
            owner_token TEXT NOT NULL,
            generation INTEGER NOT NULL CHECK (generation >= 1),
            acquired_at TEXT NOT NULL,
            heartbeat_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            active INTEGER NOT NULL CHECK (active IN (0, 1))
        );

        CREATE TABLE queue_entries (
            logical_request_id TEXT PRIMARY KEY REFERENCES logical_requests(logical_request_id),
            rate_domain TEXT NOT NULL,
            task_id TEXT NOT NULL,
            enqueued_at TEXT NOT NULL,
            state TEXT NOT NULL CHECK (state IN ('queued', 'dequeued', 'rejected', 'cancelled')),
            dequeued_at TEXT
        );

        CREATE TABLE provider_queue_cursors (
            rate_domain TEXT PRIMARY KEY,
            last_task_id TEXT NOT NULL
        );

        CREATE TABLE queue_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            logical_request_id TEXT NOT NULL REFERENCES logical_requests(logical_request_id),
            event_type TEXT NOT NULL CHECK (event_type IN ('requested', 'queued', 'dequeued', 'rejected')),
            occurred_at TEXT NOT NULL,
            reason TEXT
        );

        PRAGMA user_version = 3;
        COMMIT;
        """
    )


def _migrate_v1_to_v2(connection: sqlite3.Connection) -> None:
    metadata = connection.execute(
        "SELECT schema_owner, schema_version FROM schema_metadata WHERE singleton = 1"
    ).fetchone()
    if metadata != (_SCHEMA_OWNER, 1):
        raise RuntimeStorageError("runtime_schema_metadata_mismatch")
    connection.execute(
        "ALTER TABLE runtime_lease ADD COLUMN active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1))"
    )
    connection.execute("UPDATE schema_metadata SET schema_version = 2 WHERE singleton = 1")
    connection.execute("PRAGMA user_version = 2")


def _migrate_v2_to_v3(connection: sqlite3.Connection) -> None:
    metadata = connection.execute(
        "SELECT schema_owner, schema_version FROM schema_metadata WHERE singleton = 1"
    ).fetchone()
    if metadata != (_SCHEMA_OWNER, 2):
        raise RuntimeStorageError("runtime_schema_metadata_mismatch")
    connection.executescript(
        """
        BEGIN IMMEDIATE;

        CREATE TABLE queue_entries (
            logical_request_id TEXT PRIMARY KEY REFERENCES logical_requests(logical_request_id),
            rate_domain TEXT NOT NULL,
            task_id TEXT NOT NULL,
            enqueued_at TEXT NOT NULL,
            state TEXT NOT NULL CHECK (state IN ('queued', 'dequeued', 'rejected', 'cancelled')),
            dequeued_at TEXT
        );
        CREATE TABLE provider_queue_cursors (
            rate_domain TEXT PRIMARY KEY,
            last_task_id TEXT NOT NULL
        );
        CREATE TABLE queue_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            logical_request_id TEXT NOT NULL REFERENCES logical_requests(logical_request_id),
            event_type TEXT NOT NULL CHECK (event_type IN ('requested', 'queued', 'dequeued', 'rejected')),
            occurred_at TEXT NOT NULL,
            reason TEXT
        );
        UPDATE schema_metadata SET schema_version = 3 WHERE singleton = 1;
        PRAGMA user_version = 3;
        COMMIT;
        """
    )


def _verify_schema(connection: sqlite3.Connection) -> None:
    if int(connection.execute("PRAGMA user_version").fetchone()[0]) != SCHEMA_VERSION:
        raise RuntimeStorageError("unsupported_runtime_schema")
    row = connection.execute("SELECT schema_owner, schema_version FROM schema_metadata WHERE singleton = 1").fetchone()
    if row != (_SCHEMA_OWNER, SCHEMA_VERSION):
        raise RuntimeStorageError("runtime_schema_metadata_mismatch")
    integrity = connection.execute("PRAGMA quick_check").fetchone()
    if integrity != ("ok",):
        raise RuntimeStorageError("runtime_database_integrity_failed")


def _validate_runtime_root(runtime_root: Path) -> None:
    if runtime_root.is_symlink() or not runtime_root.is_dir():
        raise RuntimeStorageError("invalid_runtime_root")
    details = runtime_root.stat()
    if details.st_uid != os.geteuid() or stat.S_IMODE(details.st_mode) != 0o700:
        raise RuntimeStorageError("invalid_runtime_root_permissions")


def _validate_database_mode(database_path: Path) -> None:
    details = database_path.stat()
    if details.st_uid != os.geteuid() or stat.S_IMODE(details.st_mode) != 0o600:
        raise RuntimeStorageError("invalid_runtime_database_permissions")


def _validate_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() != timedelta(0):
        raise RuntimeStorageError("invalid_runtime_clock")
    return value.astimezone(UTC)


def _timestamp(value: datetime) -> str:
    return value.isoformat()


def _parse_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise RuntimeStorageError("invalid_persisted_timestamp") from error
    return _validate_utc(parsed)
