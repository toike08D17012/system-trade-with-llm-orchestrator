"""Versioned SQLite storage for the production request runtime."""

import os
import sqlite3
import stat
from datetime import UTC, datetime, timedelta
from math import isfinite
from pathlib import Path
from typing import Literal

from pydantic import TypeAdapter

from stock_research_llm_orchestrator.contracts.base import Identifier, Sha256Hex
from stock_research_llm_orchestrator.requests.production import (
    AdmissionDecision,
    CommittedRawReference,
    GateKeys,
    GateReservation,
    HierarchicalGatePolicy,
    LogicalResultOutcome,
    ProductionCachePolicy,
    ProductionLogicalRequest,
    ProductionLogicalResult,
    ProductionPhysicalAttempt,
    QueueClaim,
    QueuePolicy,
    RawPublicationIntent,
    RawPublicationRecord,
    RuntimeLease,
    RuntimeLeasePolicy,
)


SCHEMA_VERSION = 7
DATABASE_FILENAME = "request-coordinator.sqlite3"
_SCHEMA_OWNER = "production-request-coordinator"
_OWNER_TOKEN_ADAPTER = TypeAdapter(Identifier)
_RAW_STATE_ADAPTER: TypeAdapter[Literal["staging", "committed", "reconciliation_required", "failed"]] = TypeAdapter(
    Literal["staging", "committed", "reconciliation_required", "failed"]
)


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

    def admit_logical_request(
        self,
        request: ProductionLogicalRequest,
        rate_domain: str,
        cache_policy: ProductionCachePolicy,
        lease: RuntimeLease,
        now: datetime,
        queue_policy: QueuePolicy,
    ) -> AdmissionDecision:
        """Evaluate cache, join single-flight, then queue only its leader."""
        request = ProductionLogicalRequest.model_validate(request.model_dump(warnings=False))
        rate_domain = _OWNER_TOKEN_ADAPTER.validate_python(rate_domain, strict=True)
        cache_policy = ProductionCachePolicy.model_validate(cache_policy.model_dump(warnings=False))
        lease = RuntimeLease.model_validate(lease.model_dump(warnings=False))
        now = _validate_utc(now)
        queue_policy = QueuePolicy.model_validate(queue_policy.model_dump(warnings=False))
        cache_decision: Literal["disabled", "not_applicable"] = (
            "disabled" if cache_policy.applicable else "not_applicable"
        )
        decision: Literal["leader", "follower"]
        rejected = False
        try:
            with _connect(self._database_path) as connection:
                _verify_schema(connection)
                connection.execute("BEGIN IMMEDIATE")
                _assert_fence(connection, lease, now)
                _insert_logical_request(connection, request)
                _append_admission_event(connection, request.logical_request_id, f"cache_{cache_decision}", now, None)
                flight = connection.execute(
                    """
                    SELECT flight.leader_logical_request_id, consumer.rate_domain
                    FROM single_flights AS flight
                    JOIN single_flight_consumers AS consumer
                      ON consumer.logical_request_id = flight.leader_logical_request_id
                    WHERE flight.request_fingerprint = ? AND flight.state = 'active'
                    """,
                    (request.request_fingerprint,),
                ).fetchone()
                if flight is None:
                    decision = "leader"
                    leader_id = request.logical_request_id
                    connection.execute(
                        "INSERT INTO single_flights VALUES (?, ?, 'active')",
                        (request.request_fingerprint, leader_id),
                    )
                else:
                    decision = "follower"
                    leader_id = str(flight[0])
                    if str(flight[1]) != rate_domain:
                        raise RuntimeStorageError("single_flight_scope_mismatch")
                connection.execute(
                    """
                    INSERT INTO single_flight_consumers (
                        logical_request_id, request_fingerprint, role, state,
                        cache_decision, rate_domain, joined_at
                    ) VALUES (?, ?, ?, 'active', ?, ?, ?)
                    """,
                    (
                        request.logical_request_id,
                        request.request_fingerprint,
                        decision,
                        cache_decision,
                        rate_domain,
                        _timestamp(now),
                    ),
                )
                _append_admission_event(
                    connection, request.logical_request_id, f"single_flight_{decision}", now, leader_id
                )
                if decision == "leader":
                    counts = connection.execute(
                        """
                        SELECT COUNT(*),
                            SUM(CASE WHEN rate_domain = ? THEN 1 ELSE 0 END),
                            SUM(CASE WHEN rate_domain = ? AND task_id = ? THEN 1 ELSE 0 END)
                        FROM queue_entries WHERE state = 'queued'
                        """,
                        (rate_domain, rate_domain, request.task_id),
                    ).fetchone()
                    global_count, domain_count, task_count = (int(value or 0) for value in counts)
                    rejected = (
                        global_count >= queue_policy.global_limit
                        or domain_count >= queue_policy.rate_domain_limit
                        or task_count >= queue_policy.task_rate_domain_limit
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
                    _append_queue_event(connection, request.logical_request_id, "requested", now, None)
                    if rejected:
                        _cancel_logical(connection, request.logical_request_id, now, "queue_full", "failed")
                        connection.execute(
                            "UPDATE single_flights SET state = 'terminal' WHERE request_fingerprint = ?",
                            (request.request_fingerprint,),
                        )
                        connection.execute(
                            "UPDATE single_flight_consumers SET state = 'cancelled' WHERE logical_request_id = ?",
                            (request.logical_request_id,),
                        )
                        _append_queue_event(connection, request.logical_request_id, "rejected", now, "queue_full")
                    else:
                        _append_queue_event(connection, request.logical_request_id, "queued", now, None)
        except RuntimeStorageError:
            raise
        except (sqlite3.Error, OSError) as error:
            raise RuntimeStorageError("request_admission_failed") from error
        if rejected:
            raise RuntimeStorageError("queue_full")
        return AdmissionDecision(
            logical_request_id=request.logical_request_id,
            cache_decision=cache_decision,
            single_flight_decision=decision,
            leader_logical_request_id=leader_id,
        )

    def cancel_admitted_request(
        self, logical_request_id: str, reason: str, lease: RuntimeLease, now: datetime
    ) -> str | None:
        """Cancel a follower or an unsent leader, promoting a remaining consumer."""
        logical_request_id = _OWNER_TOKEN_ADAPTER.validate_python(logical_request_id, strict=True)
        reason = _OWNER_TOKEN_ADAPTER.validate_python(reason, strict=True)
        lease = RuntimeLease.model_validate(lease.model_dump(warnings=False))
        now = _validate_utc(now)
        promoted: str | None = None
        try:
            with _connect(self._database_path) as connection:
                _verify_schema(connection)
                connection.execute("BEGIN IMMEDIATE")
                _assert_fence(connection, lease, now)
                consumer = connection.execute(
                    """
                    SELECT request_fingerprint, role, state FROM single_flight_consumers
                    WHERE logical_request_id = ?
                    """,
                    (logical_request_id,),
                ).fetchone()
                if consumer is None or str(consumer[2]) != "active":
                    raise RuntimeStorageError("single_flight_consumer_not_active")
                fingerprint, role = str(consumer[0]), str(consumer[1])
                if role == "follower":
                    _cancel_logical(connection, logical_request_id, now, reason, "cancelled")
                    connection.execute(
                        "UPDATE single_flight_consumers SET state = 'cancelled' WHERE logical_request_id = ?",
                        (logical_request_id,),
                    )
                    _append_admission_event(connection, logical_request_id, "follower_cancelled", now, reason)
                else:
                    queue = connection.execute(
                        "SELECT rate_domain, state FROM queue_entries WHERE logical_request_id = ?",
                        (logical_request_id,),
                    ).fetchone()
                    attempts = connection.execute(
                        "SELECT physical_attempt_id, state FROM physical_attempts "
                        "WHERE logical_request_id = ? ORDER BY sequence_number",
                        (logical_request_id,),
                    ).fetchall()
                    active = next((item for item in attempts if str(item[1]) in {"started", "reserved"}), None)
                    already_sent = any(str(item[1]) in {"succeeded", "failed", "unknown"} for item in attempts)
                    if active is not None and str(active[1]) == "started":
                        _cancel_started_flight(connection, fingerprint, now, reason)
                        continue_cancellation = False
                    else:
                        continue_cancellation = True
                    if not continue_cancellation:
                        return None
                    if queue is None or str(queue[1]) not in {"queued", "dequeued"}:
                        raise RuntimeStorageError("in_flight_cancellation_requires_transport_result")
                    if active is not None:
                        if str(active[1]) != "reserved":
                            raise RuntimeStorageError("in_flight_cancellation_requires_transport_result")
                        _release_gate_occupancy(connection, logical_request_id, now, "leader_cancelled")
                        connection.execute(
                            "UPDATE physical_attempts SET state = 'failed' WHERE physical_attempt_id = ?",
                            (active[0],),
                        )
                    if already_sent:
                        _cancel_started_flight(connection, fingerprint, now, reason)
                        return None
                    follower = connection.execute(
                        """
                        SELECT consumer.logical_request_id, consumer.rate_domain, request.task_id
                        FROM single_flight_consumers AS consumer
                        JOIN logical_requests AS request USING (logical_request_id)
                        WHERE consumer.request_fingerprint = ? AND consumer.role = 'follower'
                          AND consumer.state = 'active'
                        ORDER BY consumer.joined_at, consumer.logical_request_id LIMIT 1
                        """,
                        (fingerprint,),
                    ).fetchone()
                    _cancel_logical(connection, logical_request_id, now, reason, "cancelled")
                    connection.execute(
                        "UPDATE single_flight_consumers SET state = 'cancelled' WHERE logical_request_id = ?",
                        (logical_request_id,),
                    )
                    connection.execute(
                        "UPDATE queue_entries SET state = 'cancelled' WHERE logical_request_id = ?",
                        (logical_request_id,),
                    )
                    if follower is None:
                        connection.execute(
                            "UPDATE single_flights SET state = 'terminal' WHERE request_fingerprint = ?",
                            (fingerprint,),
                        )
                    else:
                        promoted, promoted_domain, promoted_task = (str(value) for value in follower)
                        connection.execute(
                            "UPDATE single_flight_consumers SET role = 'leader' WHERE logical_request_id = ?",
                            (promoted,),
                        )
                        connection.execute(
                            "UPDATE single_flights SET leader_logical_request_id = ? WHERE request_fingerprint = ?",
                            (promoted, fingerprint),
                        )
                        connection.execute(
                            """
                            INSERT INTO queue_entries VALUES (?, ?, ?, ?, 'queued', NULL)
                            """,
                            (promoted, promoted_domain, promoted_task, _timestamp(now)),
                        )
                        _append_queue_event(connection, promoted, "queued", now, "leader_promoted")
                        _append_admission_event(connection, promoted, "leader_promoted", now, logical_request_id)
                    _append_admission_event(connection, logical_request_id, "leader_cancelled", now, reason)
        except RuntimeStorageError:
            raise
        except (sqlite3.Error, OSError) as error:
            raise RuntimeStorageError("request_cancellation_failed") from error
        return promoted

    def admission_events(self, logical_request_id: str) -> tuple[tuple[str, str | None], ...]:
        """Return cache, single-flight, and cancellation audit events."""
        try:
            with _connect(self._database_path) as connection:
                _verify_schema(connection)
                rows = connection.execute(
                    """
                    SELECT event_type, reason FROM admission_events
                    WHERE logical_request_id = ? ORDER BY event_id
                    """,
                    (logical_request_id,),
                ).fetchall()
        except (sqlite3.Error, OSError) as error:
            raise RuntimeStorageError("admission_event_read_failed") from error
        return tuple((str(event), None if reason is None else str(reason)) for event, reason in rows)

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

    def assert_claimed_logical_request(
        self,
        logical_request_id: str,
        *,
        task_id: str,
        source_id: str,
        operation: str,
        source_approval_version: int,
        source_profile_version: int,
        credential_scope_alias: str | None,
        egress_scope: str,
        rate_domain: str,
        lease: RuntimeLease,
        now: datetime,
        request_fingerprint: str | None = None,
    ) -> None:
        """Assert the exact persisted lineage and claimed queue identity under one fence."""
        logical_request_id = _OWNER_TOKEN_ADAPTER.validate_python(logical_request_id, strict=True)
        task_id = _OWNER_TOKEN_ADAPTER.validate_python(task_id, strict=True)
        source_id = _OWNER_TOKEN_ADAPTER.validate_python(source_id, strict=True)
        operation = _OWNER_TOKEN_ADAPTER.validate_python(operation, strict=True)
        if type(source_approval_version) is not int or source_approval_version < 1:
            raise RuntimeStorageError("invalid_source_approval_version")
        if type(source_profile_version) is not int or source_profile_version < 1:
            raise RuntimeStorageError("invalid_source_profile_version")
        if credential_scope_alias is not None:
            credential_scope_alias = _OWNER_TOKEN_ADAPTER.validate_python(credential_scope_alias, strict=True)
        egress_scope = _OWNER_TOKEN_ADAPTER.validate_python(egress_scope, strict=True)
        rate_domain = _OWNER_TOKEN_ADAPTER.validate_python(rate_domain, strict=True)
        lease = RuntimeLease.model_validate(lease.model_dump(warnings=False))
        now = _validate_utc(now)
        if request_fingerprint is not None:
            request_fingerprint = TypeAdapter(Sha256Hex).validate_python(request_fingerprint, strict=True)
        expected = (
            task_id,
            source_id,
            operation,
            source_approval_version,
            source_profile_version,
            credential_scope_alias,
            egress_scope,
            "queued",
            rate_domain,
            task_id,
            "dequeued",
        )
        try:
            with _connect(self._database_path) as connection:
                _verify_schema(connection)
                connection.execute("BEGIN")
                _assert_fence(connection, lease, now)
                row = connection.execute(
                    """
                    SELECT request.task_id, request.source_id, request.operation,
                           request.source_approval_version, request.source_profile_version,
                           request.credential_scope_alias, request.egress_scope, request.state,
                           queue.rate_domain, queue.task_id, queue.state
                    FROM logical_requests AS request
                    JOIN queue_entries AS queue USING (logical_request_id)
                    WHERE request.logical_request_id = ?
                    """,
                    (logical_request_id,),
                ).fetchone()
                if row != expected:
                    raise RuntimeStorageError("logical_request_lineage_mismatch")
                if request_fingerprint is not None:
                    consumer = connection.execute(
                        """
                        SELECT request.request_fingerprint, consumer.request_fingerprint,
                               consumer.role, consumer.state, consumer.rate_domain,
                               flight.leader_logical_request_id, flight.state
                        FROM logical_requests AS request
                        JOIN single_flight_consumers AS consumer USING (logical_request_id)
                        JOIN single_flights AS flight
                          ON flight.request_fingerprint = consumer.request_fingerprint
                        WHERE request.logical_request_id = ?
                        """,
                        (logical_request_id,),
                    ).fetchone()
                    if consumer != (
                        request_fingerprint,
                        request_fingerprint,
                        "leader",
                        "active",
                        rate_domain,
                        logical_request_id,
                        "active",
                    ):
                        raise RuntimeStorageError("logical_request_consumer_mismatch")
        except RuntimeStorageError:
            raise
        except (sqlite3.Error, OSError) as error:
            raise RuntimeStorageError("logical_request_lineage_read_failed") from error

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

    def acquire_gates(
        self,
        reservation_id: str,
        attempt: ProductionPhysicalAttempt,
        keys: GateKeys,
        policy: HierarchicalGatePolicy,
        lease: RuntimeLease,
        now: datetime,
    ) -> GateReservation:
        """Atomically acquire every scope and create the physical attempt."""
        reservation_id = _OWNER_TOKEN_ADAPTER.validate_python(reservation_id, strict=True)
        attempt = ProductionPhysicalAttempt.model_validate(attempt.model_dump(warnings=False))
        keys = GateKeys.model_validate(keys.model_dump(warnings=False))
        policy = HierarchicalGatePolicy.model_validate(policy.model_dump(warnings=False))
        lease = RuntimeLease.model_validate(lease.model_dump(warnings=False))
        now = _validate_utc(now)
        if attempt.lease_generation != lease.generation:
            raise RuntimeStorageError("gate_attempt_generation_mismatch")
        blocked_reason: str | None = None
        blocked_scope: tuple[str, str] | None = None
        try:
            with _connect(self._database_path) as connection:
                _verify_schema(connection)
                connection.execute("BEGIN IMMEDIATE")
                _assert_fence(connection, lease, now)
                queue_state = connection.execute(
                    "SELECT state FROM queue_entries WHERE logical_request_id = ?",
                    (attempt.logical_request_id,),
                ).fetchone()
                if queue_state != ("dequeued",):
                    raise RuntimeStorageError("gate_request_not_dequeued")
                logical_state = connection.execute(
                    "SELECT state FROM logical_requests WHERE logical_request_id = ?",
                    (attempt.logical_request_id,),
                ).fetchone()
                if logical_state != ("queued",):
                    raise RuntimeStorageError("gate_request_not_active")
                for scope, key_alias in keys.ordered():
                    limit = policy.limits[scope]
                    row = connection.execute(
                        """
                        SELECT active_count, last_started_at, cooldown_until
                        FROM gate_state WHERE scope = ? AND key_alias = ?
                        """,
                        (scope.value, key_alias),
                    ).fetchone()
                    active_count = 0 if row is None else int(row[0])
                    last_started = None if row is None or row[1] is None else _parse_timestamp(str(row[1]))
                    cooldown = None if row is None or row[2] is None else _parse_timestamp(str(row[2]))
                    cutoff = now - timedelta(seconds=limit.window_seconds)
                    starts = int(
                        connection.execute(
                            """
                            SELECT COUNT(*) FROM gate_starts
                            WHERE scope = ? AND key_alias = ? AND started_at > ?
                            """,
                            (scope.value, key_alias, _timestamp(cutoff)),
                        ).fetchone()[0]
                    )
                    if cooldown is not None and now < cooldown:
                        blocked_reason = "cooldown"
                    elif active_count >= limit.max_concurrency:
                        blocked_reason = "concurrency"
                    elif last_started is not None and now < last_started + timedelta(
                        seconds=limit.min_interval_seconds
                    ):
                        blocked_reason = "minimum_interval"
                    elif starts >= limit.requests_per_window:
                        blocked_reason = "rolling_window"
                    if blocked_reason is not None:
                        blocked_scope = (scope.value, key_alias)
                        _append_gate_event(
                            connection,
                            attempt.logical_request_id,
                            None,
                            scope.value,
                            key_alias,
                            "cooldown" if blocked_reason == "cooldown" else "blocked",
                            now,
                            blocked_reason,
                        )
                        break
                if blocked_reason is None:
                    existing = connection.execute(
                        """
                        SELECT logical_request_id, sequence_number, lease_generation, created_at, state
                        FROM physical_attempts WHERE physical_attempt_id = ?
                        """,
                        (attempt.physical_attempt_id,),
                    ).fetchone()
                    if existing is None:
                        expected_sequence = int(
                            connection.execute(
                                "SELECT COALESCE(MAX(sequence_number), 0) + 1 "
                                "FROM physical_attempts WHERE logical_request_id = ?",
                                (attempt.logical_request_id,),
                            ).fetchone()[0]
                        )
                        if attempt.sequence_number != expected_sequence:
                            raise RuntimeStorageError("physical_attempt_sequence_rejected")
                        connection.execute(
                            """
                            INSERT INTO physical_attempts (
                                physical_attempt_id, logical_request_id, sequence_number,
                                lease_generation, created_at, state
                            ) VALUES (?, ?, ?, ?, ?, 'reserved')
                            """,
                            (
                                attempt.physical_attempt_id,
                                attempt.logical_request_id,
                                attempt.sequence_number,
                                attempt.lease_generation,
                                attempt.created_at,
                            ),
                        )
                    elif existing != (
                        attempt.logical_request_id,
                        attempt.sequence_number,
                        attempt.lease_generation,
                        attempt.created_at,
                        "reserved",
                    ):
                        raise RuntimeStorageError("physical_attempt_reservation_mismatch")
                    connection.execute(
                        """
                        INSERT INTO gate_reservations VALUES (?, ?, ?, ?, ?, 'active')
                        """,
                        (
                            reservation_id,
                            attempt.physical_attempt_id,
                            attempt.logical_request_id,
                            lease.generation,
                            _timestamp(now),
                        ),
                    )
                    for scope, key_alias in keys.ordered():
                        connection.execute(
                            """
                            INSERT INTO gate_state (scope, key_alias, active_count, last_started_at, cooldown_until)
                            VALUES (?, ?, 1, ?, NULL)
                            ON CONFLICT(scope, key_alias) DO UPDATE SET
                                active_count = active_count + 1,
                                last_started_at = excluded.last_started_at
                            """,
                            (scope.value, key_alias, _timestamp(now)),
                        )
                        connection.execute(
                            "INSERT INTO gate_reservation_scopes VALUES (?, ?, ?)",
                            (reservation_id, scope.value, key_alias),
                        )
                        connection.execute(
                            """
                            INSERT INTO gate_starts (reservation_id, scope, key_alias, started_at)
                            VALUES (?, ?, ?, ?)
                            """,
                            (reservation_id, scope.value, key_alias, _timestamp(now)),
                        )
                        _append_gate_event(
                            connection,
                            attempt.logical_request_id,
                            reservation_id,
                            scope.value,
                            key_alias,
                            "acquired",
                            now,
                            None,
                        )
        except RuntimeStorageError:
            raise
        except (sqlite3.Error, OSError) as error:
            raise RuntimeStorageError("gate_acquisition_failed") from error
        if blocked_reason is not None:
            scope_name = "unknown" if blocked_scope is None else blocked_scope[0]
            raise RuntimeStorageError(f"gate_blocked:{scope_name}:{blocked_reason}")
        return GateReservation(
            reservation_id=reservation_id,
            physical_attempt_id=attempt.physical_attempt_id,
            logical_request_id=attempt.logical_request_id,
            lease_generation=lease.generation,
            acquired_at=_timestamp(now),
        )

    def release_gates(
        self,
        reservation: GateReservation,
        outcome: str,
        lease: RuntimeLease,
        now: datetime,
        *,
        raw_eligible: bool | None = None,
    ) -> None:
        """Release concurrency reservations and persist a terminal attempt outcome."""
        if outcome not in {"succeeded", "failed"}:
            raise RuntimeStorageError("invalid_gate_release_outcome")
        eligible = outcome == "succeeded" if raw_eligible is None else raw_eligible
        if outcome == "succeeded" and not eligible:
            raise RuntimeStorageError("successful_attempt_requires_raw_candidate")
        self._finish_gate_reservation(reservation, outcome, None, eligible, lease, now)

    def record_retry_after(
        self,
        reservation: GateReservation,
        retry_after_seconds: float,
        lease: RuntimeLease,
        now: datetime,
    ) -> None:
        """Persist provider cooldown and fail without scheduling an automatic retry."""
        if not isfinite(retry_after_seconds) or retry_after_seconds < 0:
            raise RuntimeStorageError("invalid_retry_after")
        self._finish_gate_reservation(reservation, "failed", retry_after_seconds, True, lease, now)

    def record_unknown_outcome(self, reservation: GateReservation, lease: RuntimeLease, now: datetime) -> None:
        """Release gates while preserving an indeterminate physical outcome."""
        self._finish_gate_reservation(reservation, "unknown", None, False, lease, now)

    def _finish_gate_reservation(
        self,
        reservation: GateReservation,
        outcome: str,
        retry_after_seconds: float | None,
        raw_eligible: bool,
        lease: RuntimeLease,
        now: datetime,
    ) -> None:
        reservation = GateReservation.model_validate(reservation.model_dump(warnings=False))
        lease = RuntimeLease.model_validate(lease.model_dump(warnings=False))
        now = _validate_utc(now)
        try:
            with _connect(self._database_path) as connection:
                _verify_schema(connection)
                connection.execute("BEGIN IMMEDIATE")
                _assert_fence(connection, lease, now)
                scopes = connection.execute(
                    """
                    SELECT scope, key_alias FROM gate_reservation_scopes
                    WHERE reservation_id = ? ORDER BY scope
                    """,
                    (reservation.reservation_id,),
                ).fetchall()
                current = connection.execute(
                    """
                    SELECT state, logical_request_id, physical_attempt_id, lease_generation
                    FROM gate_reservations WHERE reservation_id = ?
                    """,
                    (reservation.reservation_id,),
                ).fetchone()
                if (
                    current
                    != (
                        "active",
                        reservation.logical_request_id,
                        reservation.physical_attempt_id,
                        lease.generation,
                    )
                    or len(scopes) != 8
                ):
                    raise RuntimeStorageError("stale_gate_reservation")
                for scope, key_alias in scopes:
                    updated = connection.execute(
                        """
                        UPDATE gate_state SET active_count = active_count - 1
                        WHERE scope = ? AND key_alias = ? AND active_count > 0
                        """,
                        (scope, key_alias),
                    )
                    if updated.rowcount != 1:
                        raise RuntimeStorageError("gate_state_inconsistent")
                connection.execute(
                    "UPDATE gate_reservations SET state = 'released' WHERE reservation_id = ?",
                    (reservation.reservation_id,),
                )
                connection.execute(
                    """
                    UPDATE physical_attempts SET state = ?, raw_eligible = ?
                    WHERE physical_attempt_id = ? AND state IN ('reserved', 'started')
                    """,
                    (outcome, int(raw_eligible), reservation.physical_attempt_id),
                )
                if retry_after_seconds is not None:
                    provider = next((str(key) for scope, key in scopes if scope == "provider"), None)
                    if provider is None:
                        raise RuntimeStorageError("gate_state_inconsistent")
                    deadline = now + timedelta(seconds=retry_after_seconds)
                    connection.execute(
                        """
                        UPDATE gate_state SET cooldown_until = CASE
                            WHEN cooldown_until IS NULL OR cooldown_until < ? THEN ? ELSE cooldown_until END
                        WHERE scope = 'provider' AND key_alias = ?
                        """,
                        (_timestamp(deadline), _timestamp(deadline), provider),
                    )
                    _append_gate_event(
                        connection,
                        reservation.logical_request_id,
                        reservation.reservation_id,
                        "provider",
                        provider,
                        "cooldown",
                        now,
                        "retry_after",
                    )
                _append_gate_event(
                    connection,
                    reservation.logical_request_id,
                    reservation.reservation_id,
                    None,
                    None,
                    "released",
                    now,
                    outcome,
                )
        except RuntimeStorageError:
            raise
        except (sqlite3.Error, OSError) as error:
            raise RuntimeStorageError("gate_release_failed") from error

    def gate_events(self, logical_request_id: str) -> tuple[tuple[str, str | None, str | None], ...]:
        """Return sanitized gate audit events."""
        try:
            with _connect(self._database_path) as connection:
                _verify_schema(connection)
                rows = connection.execute(
                    """
                    SELECT event_type, scope, reason FROM gate_events
                    WHERE logical_request_id = ? ORDER BY event_id
                    """,
                    (logical_request_id,),
                ).fetchall()
        except (sqlite3.Error, OSError) as error:
            raise RuntimeStorageError("gate_event_read_failed") from error
        return tuple(
            (str(event), None if scope is None else str(scope), None if reason is None else str(reason))
            for event, scope, reason in rows
        )

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

    def reserve_next_physical_attempt(
        self,
        physical_attempt_id: str,
        logical_request_id: str,
        lease: RuntimeLease,
        now: datetime,
    ) -> ProductionPhysicalAttempt:
        """Transactionally allocate the next sequence for one active logical request."""
        physical_attempt_id = _OWNER_TOKEN_ADAPTER.validate_python(physical_attempt_id, strict=True)
        logical_request_id = _OWNER_TOKEN_ADAPTER.validate_python(logical_request_id, strict=True)
        lease = RuntimeLease.model_validate(lease.model_dump(warnings=False))
        now = _validate_utc(now)
        try:
            with _connect(self._database_path) as connection:
                _verify_schema(connection)
                connection.execute("BEGIN IMMEDIATE")
                _assert_fence(connection, lease, now)
                logical = connection.execute(
                    "SELECT state FROM logical_requests WHERE logical_request_id = ?",
                    (logical_request_id,),
                ).fetchone()
                if logical != ("queued",):
                    raise RuntimeStorageError("physical_attempt_logical_not_active")
                unknown = connection.execute(
                    "SELECT 1 FROM physical_attempts WHERE logical_request_id = ? AND state = 'unknown' LIMIT 1",
                    (logical_request_id,),
                ).fetchone()
                if unknown is not None:
                    raise RuntimeStorageError("physical_attempt_already_unknown")
                active = connection.execute(
                    """
                    SELECT 1 FROM physical_attempts
                    WHERE logical_request_id = ? AND state IN ('reserved', 'started') LIMIT 1
                    """,
                    (logical_request_id,),
                ).fetchone()
                if active is not None:
                    raise RuntimeStorageError("physical_attempt_already_active")
                sequence_number = int(
                    connection.execute(
                        "SELECT COALESCE(MAX(sequence_number), 0) + 1 "
                        "FROM physical_attempts WHERE logical_request_id = ?",
                        (logical_request_id,),
                    ).fetchone()[0]
                )
                created_at = _timestamp(now)
                connection.execute(
                    """
                    INSERT INTO physical_attempts (
                        physical_attempt_id, logical_request_id, sequence_number,
                        lease_generation, created_at, state
                    ) VALUES (?, ?, ?, ?, ?, 'reserved')
                    """,
                    (physical_attempt_id, logical_request_id, sequence_number, lease.generation, created_at),
                )
        except RuntimeStorageError:
            raise
        except (sqlite3.Error, OSError) as error:
            raise RuntimeStorageError("physical_attempt_reservation_failed") from error
        return ProductionPhysicalAttempt(
            physical_attempt_id=physical_attempt_id,
            logical_request_id=logical_request_id,
            sequence_number=sequence_number,
            lease_generation=lease.generation,
            created_at=created_at,
        )

    def discard_reserved_attempt(self, physical_attempt_id: str, lease: RuntimeLease, now: datetime) -> None:
        """Discard an unsent reservation that never acquired any durable gate reservation."""
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
                    DELETE FROM physical_attempts
                    WHERE physical_attempt_id = ? AND lease_generation = ? AND state = 'reserved'
                      AND NOT EXISTS (
                          SELECT 1 FROM gate_reservations
                          WHERE gate_reservations.physical_attempt_id = physical_attempts.physical_attempt_id
                      )
                    """,
                    (physical_attempt_id, lease.generation),
                )
                if updated.rowcount != 1:
                    raise RuntimeStorageError("physical_attempt_discard_rejected")
        except RuntimeStorageError:
            raise
        except (sqlite3.Error, OSError) as error:
            raise RuntimeStorageError("physical_attempt_discard_failed") from error

    def begin_raw_publication(self, intent: RawPublicationIntent, lease: RuntimeLease, now: datetime) -> None:
        """Persist a fenced publication intent before writing candidate bytes."""
        intent = RawPublicationIntent.model_validate(intent.model_dump(warnings=False))
        lease = RuntimeLease.model_validate(lease.model_dump(warnings=False))
        now = _validate_utc(now)
        try:
            with _connect(self._database_path) as connection:
                _verify_schema(connection)
                connection.execute("BEGIN IMMEDIATE")
                _assert_fence(connection, lease, now)
                match = connection.execute(
                    """
                    SELECT 1 FROM physical_attempts AS attempt
                    JOIN logical_requests AS request USING (logical_request_id)
                    WHERE attempt.physical_attempt_id = ? AND attempt.logical_request_id = ?
                      AND request.task_id = ? AND request.source_id = ? AND request.operation = ?
                      AND attempt.state IN ('succeeded', 'failed') AND attempt.raw_eligible = 1
                    """,
                    (
                        intent.physical_attempt_id,
                        intent.logical_request_id,
                        intent.task_id,
                        intent.source_id,
                        intent.operation,
                    ),
                ).fetchone()
                if match is None:
                    raise RuntimeStorageError("raw_publication_attempt_mismatch")
                connection.execute(
                    """
                    INSERT INTO raw_publications (
                        publication_id, task_id, logical_request_id, physical_attempt_id,
                        source_id, operation, content_sha256, byte_count, media_type,
                        encoding, raw_schema_id, raw_schema_version, publication_generation,
                        relative_path, state, created_at, committed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 'staging', ?, NULL)
                    """,
                    (
                        intent.publication_id,
                        intent.task_id,
                        intent.logical_request_id,
                        intent.physical_attempt_id,
                        intent.source_id,
                        intent.operation,
                        intent.content_sha256,
                        intent.byte_count,
                        intent.media_type,
                        intent.encoding,
                        intent.raw_schema_id,
                        intent.raw_schema_version,
                        intent.publication_generation,
                        _timestamp(now),
                    ),
                )
        except RuntimeStorageError:
            raise
        except (sqlite3.Error, OSError) as error:
            raise RuntimeStorageError("raw_publication_intent_failed") from error

    def commit_raw_publication(self, reference: CommittedRawReference, lease: RuntimeLease, now: datetime) -> None:
        """Fence and commit the reference after atomic filesystem publication."""
        reference = CommittedRawReference.model_validate(reference.model_dump(warnings=False))
        lease = RuntimeLease.model_validate(lease.model_dump(warnings=False))
        now = _validate_utc(now)
        try:
            with _connect(self._database_path) as connection:
                _verify_schema(connection)
                connection.execute("BEGIN IMMEDIATE")
                _assert_fence(connection, lease, now)
                updated = connection.execute(
                    """
                    UPDATE raw_publications SET relative_path = ?, state = 'committed', committed_at = ?
                    WHERE publication_id = ? AND task_id = ? AND logical_request_id = ?
                      AND physical_attempt_id = ? AND content_sha256 = ? AND byte_count = ?
                      AND raw_schema_id = ? AND raw_schema_version = ?
                      AND publication_generation = ? AND state IN ('staging', 'reconciliation_required')
                    """,
                    (
                        reference.relative_path,
                        _timestamp(now),
                        reference.publication_id,
                        reference.task_id,
                        reference.logical_request_id,
                        reference.physical_attempt_id,
                        reference.content_sha256,
                        reference.byte_count,
                        reference.raw_schema_id,
                        reference.raw_schema_version,
                        reference.publication_generation,
                    ),
                )
                if updated.rowcount != 1:
                    raise RuntimeStorageError("raw_publication_transition_rejected")
        except RuntimeStorageError:
            raise
        except (sqlite3.Error, OSError) as error:
            raise RuntimeStorageError("raw_publication_commit_failed") from error

    def fail_raw_publication(self, publication_id: str, lease: RuntimeLease, now: datetime) -> None:
        """Record a known pre-publication rejection without retaining raw bytes."""
        self._transition_raw_publication(publication_id, "failed", ("staging", "reconciliation_required"), lease, now)

    def raw_publication_state(self, publication_id: str) -> tuple[str, str | None] | None:
        """Read sanitized publication state and its committed relative path."""
        publication_id = _OWNER_TOKEN_ADAPTER.validate_python(publication_id, strict=True)
        try:
            with _connect(self._database_path) as connection:
                _verify_schema(connection)
                row = connection.execute(
                    "SELECT state, relative_path FROM raw_publications WHERE publication_id = ?",
                    (publication_id,),
                ).fetchone()
        except (sqlite3.Error, OSError) as error:
            raise RuntimeStorageError("raw_publication_read_failed") from error
        return None if row is None else (str(row[0]), None if row[1] is None else str(row[1]))

    def raw_publication_record(self, publication_id: str) -> RawPublicationRecord | None:
        """Load one secret-free publication record for replay or reconciliation."""
        publication_id = _OWNER_TOKEN_ADAPTER.validate_python(publication_id, strict=True)
        records = self._read_raw_publications("publication_id = ?", (publication_id,))
        return None if not records else records[0]

    def raw_publications_for_reconciliation(self) -> tuple[RawPublicationRecord, ...]:
        """Load every nonterminal publication in deterministic order."""
        return self._read_raw_publications(
            "state IN ('staging', 'reconciliation_required') ORDER BY publication_id", ()
        )

    def committed_raw_references(self, logical_request_id: str) -> tuple[CommittedRawReference, ...]:
        """Resolve all committed physical response references for one logical result."""
        logical_request_id = _OWNER_TOKEN_ADAPTER.validate_python(logical_request_id, strict=True)
        records = self._read_raw_publications(
            "logical_request_id = ? AND state = 'committed' ORDER BY physical_attempt_id",
            (logical_request_id,),
        )
        return tuple(_raw_reference(record) for record in records)

    def committed_raw_records(self) -> tuple[RawPublicationRecord, ...]:
        """Load all committed publications for filesystem integrity verification."""
        return self._read_raw_publications("state = 'committed' ORDER BY publication_id", ())

    def mark_raw_publication_reconciliation_required(
        self, publication_id: str, lease: RuntimeLease, now: datetime
    ) -> None:
        """Persist that filesystem publication may have occurred."""
        self._transition_raw_publication(publication_id, "reconciliation_required", ("staging",), lease, now)

    def _transition_raw_publication(
        self,
        publication_id: str,
        target: Literal["reconciliation_required", "failed"],
        sources: tuple[str, ...],
        lease: RuntimeLease,
        now: datetime,
    ) -> None:
        publication_id = _OWNER_TOKEN_ADAPTER.validate_python(publication_id, strict=True)
        lease = RuntimeLease.model_validate(lease.model_dump(warnings=False))
        now = _validate_utc(now)
        placeholders = ",".join("?" for _ in sources)
        try:
            with _connect(self._database_path) as connection:
                _verify_schema(connection)
                connection.execute("BEGIN IMMEDIATE")
                _assert_fence(connection, lease, now)
                updated = connection.execute(
                    f"UPDATE raw_publications SET state = ? WHERE publication_id = ? AND state IN ({placeholders})",
                    (target, publication_id, *sources),
                )
                if updated.rowcount != 1:
                    raise RuntimeStorageError("raw_publication_transition_rejected")
        except RuntimeStorageError:
            raise
        except (sqlite3.Error, OSError) as error:
            raise RuntimeStorageError("raw_publication_transition_failed") from error

    def _read_raw_publications(
        self, predicate: str, parameters: tuple[object, ...]
    ) -> tuple[RawPublicationRecord, ...]:
        try:
            with _connect(self._database_path) as connection:
                _verify_schema(connection)
                rows = connection.execute(
                    f"""
                    SELECT publication_id, task_id, logical_request_id, physical_attempt_id,
                           source_id, operation, content_sha256, byte_count, media_type,
                           encoding, raw_schema_id, raw_schema_version, publication_generation,
                           relative_path, state
                    FROM raw_publications WHERE {predicate}
                    """,
                    parameters,
                ).fetchall()
            return tuple(_raw_record(row) for row in rows)
        except (sqlite3.Error, OSError, TypeError, ValueError) as error:
            raise RuntimeStorageError("raw_publication_read_failed") from error

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

    def finalize_logical_request(self, result: ProductionLogicalResult, lease: RuntimeLease, now: datetime) -> None:
        """Finalize once after validating all durable physical attempt outcomes."""
        result = ProductionLogicalResult.model_validate(result.model_dump(warnings=False))
        lease = RuntimeLease.model_validate(lease.model_dump(warnings=False))
        now = _validate_utc(now)
        try:
            with _connect(self._database_path) as connection:
                _verify_schema(connection)
                connection.execute("BEGIN IMMEDIATE")
                _assert_fence(connection, lease, now)
                request = connection.execute(
                    "SELECT state FROM logical_requests WHERE logical_request_id = ?",
                    (result.logical_request_id,),
                ).fetchone()
                if request != ("queued",):
                    raise RuntimeStorageError("logical_request_not_active")
                states = tuple(
                    str(row[0])
                    for row in connection.execute(
                        "SELECT state FROM physical_attempts WHERE logical_request_id = ? ORDER BY sequence_number",
                        (result.logical_request_id,),
                    )
                )
                if any(state in {"reserved", "started"} for state in states):
                    raise RuntimeStorageError("logical_request_has_active_attempt")
                if result.outcome is LogicalResultOutcome.SUCCEEDED and (
                    not states or states[-1] != "succeeded" or "unknown" in states
                ):
                    raise RuntimeStorageError("logical_success_attempts_invalid")
                if result.outcome is LogicalResultOutcome.UNKNOWN and "unknown" not in states:
                    raise RuntimeStorageError("logical_unknown_attempt_missing")
                state = {
                    LogicalResultOutcome.SUCCEEDED: "succeeded",
                    LogicalResultOutcome.FAILED: "failed",
                    LogicalResultOutcome.CANCELLED: "cancelled",
                    LogicalResultOutcome.UNKNOWN: "failed",
                }[result.outcome]
                connection.execute(
                    "UPDATE logical_requests SET state = ? WHERE logical_request_id = ?",
                    (state, result.logical_request_id),
                )
                connection.execute(
                    """
                    INSERT INTO logical_results (logical_request_id, outcome, completed_at, error_code)
                    VALUES (?, ?, ?, ?)
                    """,
                    (result.logical_request_id, result.outcome.value, result.completed_at, result.error_code),
                )
        except RuntimeStorageError:
            raise
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
                _recover_gate_reservations(connection, previous_generation, now)
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
                interrupted_between_attempts = connection.execute(
                    """
                    SELECT DISTINCT attempt.logical_request_id
                    FROM physical_attempts AS attempt
                    JOIN logical_requests AS request USING (logical_request_id)
                    WHERE request.state = 'queued' AND attempt.state IN ('succeeded', 'failed')
                      AND NOT EXISTS (
                          SELECT 1 FROM physical_attempts AS active
                          WHERE active.logical_request_id = attempt.logical_request_id
                            AND active.state IN ('reserved', 'started')
                      )
                    """
                ).fetchall()
                for (logical_request_id,) in interrupted_between_attempts:
                    connection.execute(
                        "UPDATE logical_requests SET state = 'failed' WHERE logical_request_id = ?",
                        (logical_request_id,),
                    )
                    connection.execute(
                        """
                        INSERT INTO logical_results (logical_request_id, outcome, completed_at, error_code)
                        VALUES (?, 'failed', ?, 'owner_interrupted_between_attempts')
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


def _append_gate_event(
    connection: sqlite3.Connection,
    logical_request_id: str,
    reservation_id: str | None,
    scope: str | None,
    key_alias: str | None,
    event_type: str,
    now: datetime,
    reason: str | None,
) -> None:
    connection.execute(
        """
        INSERT INTO gate_events (
            logical_request_id, reservation_id, scope, key_alias, event_type, occurred_at, reason
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (logical_request_id, reservation_id, scope, key_alias, event_type, _timestamp(now), reason),
    )


def _append_admission_event(
    connection: sqlite3.Connection,
    logical_request_id: str,
    event_type: str,
    now: datetime,
    reason: str | None,
) -> None:
    connection.execute(
        """
        INSERT INTO admission_events (logical_request_id, event_type, occurred_at, reason)
        VALUES (?, ?, ?, ?)
        """,
        (logical_request_id, event_type, _timestamp(now), reason),
    )


def _cancel_logical(
    connection: sqlite3.Connection,
    logical_request_id: str,
    now: datetime,
    reason: str,
    outcome: str,
) -> None:
    state = "cancelled" if outcome == "cancelled" else "failed"
    updated = connection.execute(
        "UPDATE logical_requests SET state = ? WHERE logical_request_id = ? AND state = 'queued'",
        (state, logical_request_id),
    )
    if updated.rowcount != 1:
        raise RuntimeStorageError("logical_request_not_cancellable")
    connection.execute(
        """
        INSERT INTO logical_results (logical_request_id, outcome, completed_at, error_code)
        VALUES (?, ?, ?, ?)
        """,
        (logical_request_id, outcome, _timestamp(now), None if outcome == "cancelled" else reason),
    )


def _release_gate_occupancy(
    connection: sqlite3.Connection, logical_request_id: str, now: datetime, reason: str
) -> None:
    reservations = connection.execute(
        """
        SELECT reservation_id FROM gate_reservations
        WHERE logical_request_id = ? AND state = 'active'
        """,
        (logical_request_id,),
    ).fetchall()
    for (reservation_id,) in reservations:
        scopes = connection.execute(
            "SELECT scope, key_alias FROM gate_reservation_scopes WHERE reservation_id = ?",
            (reservation_id,),
        ).fetchall()
        if len(scopes) != 8:
            raise RuntimeStorageError("gate_state_inconsistent")
        for scope, key_alias in scopes:
            updated = connection.execute(
                """
                UPDATE gate_state SET active_count = active_count - 1
                WHERE scope = ? AND key_alias = ? AND active_count > 0
                """,
                (scope, key_alias),
            )
            if updated.rowcount != 1:
                raise RuntimeStorageError("gate_state_inconsistent")
        connection.execute(
            "UPDATE gate_reservations SET state = 'recovered' WHERE reservation_id = ?",
            (reservation_id,),
        )
        _append_gate_event(
            connection,
            logical_request_id,
            str(reservation_id),
            None,
            None,
            "released",
            now,
            reason,
        )


def _cancel_started_flight(connection: sqlite3.Connection, fingerprint: str, now: datetime, reason: str) -> None:
    consumers = connection.execute(
        """
        SELECT logical_request_id FROM single_flight_consumers
        WHERE request_fingerprint = ? AND state = 'active'
        """,
        (fingerprint,),
    ).fetchall()
    leader = connection.execute(
        "SELECT leader_logical_request_id FROM single_flights WHERE request_fingerprint = ? AND state = 'active'",
        (fingerprint,),
    ).fetchone()
    if leader is None:
        raise RuntimeStorageError("single_flight_state_inconsistent")
    leader_id = str(leader[0])
    _release_gate_occupancy(connection, leader_id, now, "in_flight_cancelled")
    connection.execute(
        "UPDATE physical_attempts SET state = 'unknown' WHERE logical_request_id = ? AND state = 'started'",
        (leader_id,),
    )
    for (logical_request_id,) in consumers:
        consumer_id = str(logical_request_id)
        _cancel_logical(connection, consumer_id, now, reason, "unknown")
        connection.execute(
            "UPDATE single_flight_consumers SET state = 'cancelled' WHERE logical_request_id = ?",
            (consumer_id,),
        )
        _append_admission_event(connection, consumer_id, "in_flight_cancelled", now, reason)
    connection.execute(
        "UPDATE single_flights SET state = 'terminal' WHERE request_fingerprint = ?",
        (fingerprint,),
    )


def _recover_gate_reservations(connection: sqlite3.Connection, generation: int, now: datetime) -> None:
    reservations = connection.execute(
        """
        SELECT reservation_id, logical_request_id FROM gate_reservations
        WHERE state = 'active' AND lease_generation <= ?
        """,
        (generation,),
    ).fetchall()
    for reservation_id, logical_request_id in reservations:
        scopes = connection.execute(
            "SELECT scope, key_alias FROM gate_reservation_scopes WHERE reservation_id = ?",
            (reservation_id,),
        ).fetchall()
        if len(scopes) != 8:
            raise RuntimeStorageError("runtime_recovery_audit_gap")
        for scope, key_alias in scopes:
            updated = connection.execute(
                """
                UPDATE gate_state SET active_count = active_count - 1
                WHERE scope = ? AND key_alias = ? AND active_count > 0
                """,
                (scope, key_alias),
            )
            if updated.rowcount != 1:
                raise RuntimeStorageError("runtime_recovery_audit_gap")
        connection.execute(
            "UPDATE gate_reservations SET state = 'recovered' WHERE reservation_id = ?",
            (reservation_id,),
        )
        _append_gate_event(
            connection,
            str(logical_request_id),
            str(reservation_id),
            None,
            None,
            "recovered",
            now,
            "owner_interrupted",
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
            AND request.state != 'queued' AND result.logical_request_id IS NULL
        ) OR (
            attempt.state = 'unknown' AND request.state = 'queued'
        ) OR (
            attempt.state IN ('succeeded', 'failed') AND request.state = 'queued'
            AND NOT EXISTS (
                SELECT 1 FROM gate_reservations AS reservation
                WHERE reservation.physical_attempt_id = attempt.physical_attempt_id
                  AND reservation.state = 'released'
            )
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
                        current_version = 3
                    if current_version == 3:
                        _migrate_v3_to_v4(connection)
                        current_version = 4
                    if current_version == 4:
                        _migrate_v4_to_v5(connection)
                        current_version = 5
                    if current_version == 5:
                        _migrate_v5_to_v6(connection)
                        current_version = 6
                    if current_version == 6:
                        _migrate_v6_to_v7(connection)
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
        INSERT INTO schema_metadata VALUES (1, 'production-request-coordinator', 7);

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
            raw_eligible INTEGER NOT NULL DEFAULT 0 CHECK (raw_eligible IN (0, 1)),
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

        CREATE TABLE gate_state (
            scope TEXT NOT NULL,
            key_alias TEXT NOT NULL,
            active_count INTEGER NOT NULL CHECK (active_count >= 0),
            last_started_at TEXT,
            cooldown_until TEXT,
            PRIMARY KEY (scope, key_alias)
        );
        CREATE TABLE gate_reservations (
            reservation_id TEXT PRIMARY KEY,
            physical_attempt_id TEXT NOT NULL UNIQUE REFERENCES physical_attempts(physical_attempt_id),
            logical_request_id TEXT NOT NULL REFERENCES logical_requests(logical_request_id),
            lease_generation INTEGER NOT NULL CHECK (lease_generation >= 1),
            acquired_at TEXT NOT NULL,
            state TEXT NOT NULL CHECK (state IN ('active', 'released', 'recovered'))
        );
        CREATE TABLE gate_reservation_scopes (
            reservation_id TEXT NOT NULL REFERENCES gate_reservations(reservation_id),
            scope TEXT NOT NULL,
            key_alias TEXT NOT NULL,
            PRIMARY KEY (reservation_id, scope),
            FOREIGN KEY (scope, key_alias) REFERENCES gate_state(scope, key_alias)
        );
        CREATE TABLE gate_starts (
            start_id INTEGER PRIMARY KEY AUTOINCREMENT,
            reservation_id TEXT NOT NULL REFERENCES gate_reservations(reservation_id),
            scope TEXT NOT NULL,
            key_alias TEXT NOT NULL,
            started_at TEXT NOT NULL
        );
        CREATE TABLE gate_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            logical_request_id TEXT NOT NULL REFERENCES logical_requests(logical_request_id),
            reservation_id TEXT,
            scope TEXT,
            key_alias TEXT,
            event_type TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            reason TEXT
        );

        CREATE TABLE single_flights (
            request_fingerprint TEXT PRIMARY KEY,
            leader_logical_request_id TEXT NOT NULL REFERENCES logical_requests(logical_request_id),
            state TEXT NOT NULL CHECK (state IN ('active', 'terminal'))
        );
        CREATE TABLE single_flight_consumers (
            logical_request_id TEXT PRIMARY KEY REFERENCES logical_requests(logical_request_id),
            request_fingerprint TEXT NOT NULL REFERENCES single_flights(request_fingerprint),
            role TEXT NOT NULL CHECK (role IN ('leader', 'follower')),
            state TEXT NOT NULL CHECK (state IN ('active', 'cancelled')),
            cache_decision TEXT NOT NULL CHECK (cache_decision IN ('disabled', 'not_applicable')),
            rate_domain TEXT NOT NULL,
            joined_at TEXT NOT NULL
        );
        CREATE TABLE admission_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            logical_request_id TEXT NOT NULL REFERENCES logical_requests(logical_request_id),
            event_type TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            reason TEXT
        );

        CREATE TABLE raw_publications (
            publication_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            logical_request_id TEXT NOT NULL REFERENCES logical_requests(logical_request_id),
            physical_attempt_id TEXT NOT NULL UNIQUE REFERENCES physical_attempts(physical_attempt_id),
            source_id TEXT NOT NULL,
            operation TEXT NOT NULL,
            content_sha256 TEXT NOT NULL,
            byte_count INTEGER NOT NULL CHECK (byte_count >= 0),
            media_type TEXT NOT NULL,
            encoding TEXT NOT NULL,
            raw_schema_id TEXT NOT NULL,
            raw_schema_version INTEGER NOT NULL CHECK (raw_schema_version >= 1),
            publication_generation INTEGER NOT NULL CHECK (publication_generation >= 1),
            relative_path TEXT,
            state TEXT NOT NULL CHECK (state IN ('staging', 'committed', 'reconciliation_required', 'failed')),
            created_at TEXT NOT NULL,
            committed_at TEXT
        );

        PRAGMA user_version = 7;
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


def _migrate_v3_to_v4(connection: sqlite3.Connection) -> None:
    metadata = connection.execute(
        "SELECT schema_owner, schema_version FROM schema_metadata WHERE singleton = 1"
    ).fetchone()
    if metadata != (_SCHEMA_OWNER, 3):
        raise RuntimeStorageError("runtime_schema_metadata_mismatch")
    connection.executescript(
        """
        BEGIN IMMEDIATE;
        CREATE TABLE gate_state (
            scope TEXT NOT NULL,
            key_alias TEXT NOT NULL,
            active_count INTEGER NOT NULL CHECK (active_count >= 0),
            last_started_at TEXT,
            cooldown_until TEXT,
            PRIMARY KEY (scope, key_alias)
        );
        CREATE TABLE gate_reservations (
            reservation_id TEXT PRIMARY KEY,
            physical_attempt_id TEXT NOT NULL UNIQUE REFERENCES physical_attempts(physical_attempt_id),
            logical_request_id TEXT NOT NULL REFERENCES logical_requests(logical_request_id),
            lease_generation INTEGER NOT NULL CHECK (lease_generation >= 1),
            acquired_at TEXT NOT NULL,
            state TEXT NOT NULL CHECK (state IN ('active', 'released', 'recovered'))
        );
        CREATE TABLE gate_reservation_scopes (
            reservation_id TEXT NOT NULL REFERENCES gate_reservations(reservation_id),
            scope TEXT NOT NULL,
            key_alias TEXT NOT NULL,
            PRIMARY KEY (reservation_id, scope),
            FOREIGN KEY (scope, key_alias) REFERENCES gate_state(scope, key_alias)
        );
        CREATE TABLE gate_starts (
            start_id INTEGER PRIMARY KEY AUTOINCREMENT,
            reservation_id TEXT NOT NULL REFERENCES gate_reservations(reservation_id),
            scope TEXT NOT NULL,
            key_alias TEXT NOT NULL,
            started_at TEXT NOT NULL
        );
        CREATE TABLE gate_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            logical_request_id TEXT NOT NULL REFERENCES logical_requests(logical_request_id),
            reservation_id TEXT,
            scope TEXT,
            key_alias TEXT,
            event_type TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            reason TEXT
        );
        UPDATE schema_metadata SET schema_version = 4 WHERE singleton = 1;
        PRAGMA user_version = 4;
        COMMIT;
        """
    )


def _migrate_v4_to_v5(connection: sqlite3.Connection) -> None:
    metadata = connection.execute(
        "SELECT schema_owner, schema_version FROM schema_metadata WHERE singleton = 1"
    ).fetchone()
    if metadata != (_SCHEMA_OWNER, 4):
        raise RuntimeStorageError("runtime_schema_metadata_mismatch")
    connection.executescript(
        """
        BEGIN IMMEDIATE;
        CREATE TABLE single_flights (
            request_fingerprint TEXT PRIMARY KEY,
            leader_logical_request_id TEXT NOT NULL REFERENCES logical_requests(logical_request_id),
            state TEXT NOT NULL CHECK (state IN ('active', 'terminal'))
        );
        CREATE TABLE single_flight_consumers (
            logical_request_id TEXT PRIMARY KEY REFERENCES logical_requests(logical_request_id),
            request_fingerprint TEXT NOT NULL REFERENCES single_flights(request_fingerprint),
            role TEXT NOT NULL CHECK (role IN ('leader', 'follower')),
            state TEXT NOT NULL CHECK (state IN ('active', 'cancelled')),
            cache_decision TEXT NOT NULL CHECK (cache_decision IN ('disabled', 'not_applicable')),
            rate_domain TEXT NOT NULL,
            joined_at TEXT NOT NULL
        );
        CREATE TABLE admission_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            logical_request_id TEXT NOT NULL REFERENCES logical_requests(logical_request_id),
            event_type TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            reason TEXT
        );
        UPDATE schema_metadata SET schema_version = 5 WHERE singleton = 1;
        PRAGMA user_version = 5;
        COMMIT;
        """
    )


def _migrate_v5_to_v6(connection: sqlite3.Connection) -> None:
    metadata = connection.execute(
        "SELECT schema_owner, schema_version FROM schema_metadata WHERE singleton = 1"
    ).fetchone()
    if metadata != (_SCHEMA_OWNER, 5):
        raise RuntimeStorageError("runtime_schema_metadata_mismatch")
    connection.executescript(
        """
        BEGIN IMMEDIATE;
        CREATE TABLE raw_publications (
            publication_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            logical_request_id TEXT NOT NULL REFERENCES logical_requests(logical_request_id),
            physical_attempt_id TEXT NOT NULL UNIQUE REFERENCES physical_attempts(physical_attempt_id),
            source_id TEXT NOT NULL,
            operation TEXT NOT NULL,
            content_sha256 TEXT NOT NULL,
            byte_count INTEGER NOT NULL CHECK (byte_count >= 0),
            media_type TEXT NOT NULL,
            encoding TEXT NOT NULL,
            raw_schema_id TEXT NOT NULL,
            raw_schema_version INTEGER NOT NULL CHECK (raw_schema_version >= 1),
            publication_generation INTEGER NOT NULL CHECK (publication_generation >= 1),
            relative_path TEXT,
            state TEXT NOT NULL CHECK (state IN ('staging', 'committed', 'reconciliation_required', 'failed')),
            created_at TEXT NOT NULL,
            committed_at TEXT
        );
        UPDATE schema_metadata SET schema_version = 6 WHERE singleton = 1;
        PRAGMA user_version = 6;
        COMMIT;
        """
    )


def _migrate_v6_to_v7(connection: sqlite3.Connection) -> None:
    """Record whether common validation produced publishable bounded bytes."""
    metadata = connection.execute(
        "SELECT schema_owner, schema_version FROM schema_metadata WHERE singleton = 1"
    ).fetchone()
    if metadata != (_SCHEMA_OWNER, 6):
        raise RuntimeStorageError("runtime_schema_metadata_mismatch")
    connection.executescript(
        """
        BEGIN IMMEDIATE;
        ALTER TABLE physical_attempts
            ADD COLUMN raw_eligible INTEGER NOT NULL DEFAULT 0 CHECK (raw_eligible IN (0, 1));
        UPDATE physical_attempts SET raw_eligible = 1 WHERE state = 'succeeded';
        UPDATE schema_metadata SET schema_version = 7 WHERE singleton = 1;
        PRAGMA user_version = 7;
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


def _raw_record(row: tuple[object, ...]) -> RawPublicationRecord:
    intent = RawPublicationIntent(
        publication_id=str(row[0]),
        task_id=str(row[1]),
        logical_request_id=str(row[2]),
        physical_attempt_id=str(row[3]),
        source_id=str(row[4]),
        operation=str(row[5]),
        content_sha256=str(row[6]),
        byte_count=int(str(row[7])),
        media_type=str(row[8]),
        encoding=str(row[9]),
        raw_schema_id=str(row[10]),
        raw_schema_version=int(str(row[11])),
        publication_generation=int(str(row[12])),
    )
    return RawPublicationRecord(
        intent=intent,
        relative_path=None if row[13] is None else str(row[13]),
        state=_RAW_STATE_ADAPTER.validate_python(row[14], strict=True),
    )


def _raw_reference(record: RawPublicationRecord) -> CommittedRawReference:
    if record.state != "committed" or record.relative_path is None:
        raise RuntimeStorageError("raw_publication_reference_not_committed")
    intent = record.intent
    return CommittedRawReference(
        publication_id=intent.publication_id,
        task_id=intent.task_id,
        logical_request_id=intent.logical_request_id,
        physical_attempt_id=intent.physical_attempt_id,
        relative_path=record.relative_path,
        content_sha256=intent.content_sha256,
        byte_count=intent.byte_count,
        raw_schema_id=intent.raw_schema_id,
        raw_schema_version=intent.raw_schema_version,
        publication_generation=intent.publication_generation,
    )


def _parse_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise RuntimeStorageError("invalid_persisted_timestamp") from error
    return _validate_utc(parsed)
