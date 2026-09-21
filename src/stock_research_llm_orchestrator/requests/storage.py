"""Versioned SQLite storage for the production request runtime."""

import os
import sqlite3
import stat
from pathlib import Path

from stock_research_llm_orchestrator.requests.production import (
    LogicalResultOutcome,
    ProductionLogicalRequest,
    ProductionLogicalResult,
    ProductionPhysicalAttempt,
)


SCHEMA_VERSION = 1
DATABASE_FILENAME = "request-coordinator.sqlite3"
_SCHEMA_OWNER = "production-request-coordinator"


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

    def complete_logical_request(self, result: ProductionLogicalResult) -> None:
        """Atomically write one result and transition its logical request."""
        result = ProductionLogicalResult.model_validate(result.model_dump(warnings=False))
        state = {
            LogicalResultOutcome.SUCCEEDED: "succeeded",
            LogicalResultOutcome.FAILED: "failed",
            LogicalResultOutcome.CANCELLED: "cancelled",
            LogicalResultOutcome.UNKNOWN: "failed",
        }[result.outcome]
        try:
            with _connect(self._database_path) as connection:
                _verify_schema(connection)
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
                elif current_version != SCHEMA_VERSION:
                    raise RuntimeStorageError("unsupported_runtime_schema")
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
        CREATE TABLE schema_metadata (
            singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
            schema_owner TEXT NOT NULL,
            schema_version INTEGER NOT NULL CHECK (schema_version >= 1)
        );
        INSERT INTO schema_metadata VALUES (1, 'production-request-coordinator', 1);

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
            expires_at TEXT NOT NULL
        );

        PRAGMA user_version = 1;
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
