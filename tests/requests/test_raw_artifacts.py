import hashlib
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from stock_research_llm_orchestrator.requests.production import (
    ProductionLogicalRequest,
    ProductionPhysicalAttempt,
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
from stock_research_llm_orchestrator.requests.transport import TemporaryRawCandidate


NOW = datetime(2026, 9, 22, tzinfo=UTC)
BODY = b'{"value":1}'
HASH = hashlib.sha256(BODY).hexdigest()


def _prepared(
    tmp_path: Path,
) -> tuple[ProductionRequestRepository, RuntimeLease, Path, TemporaryRawCandidate, RawPublicationIntent]:
    runtime = tmp_path / ".runtime"
    runtime.mkdir(mode=0o700)
    runs = tmp_path / "runs"
    runs.mkdir(mode=0o700)
    repository = initialize_runtime_storage(runtime)
    lease = repository.acquire_lease("owner-a", NOW, RuntimeLeasePolicy())
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
    repository.mark_physical_attempt_started("attempt-1", lease, NOW + timedelta(seconds=1))
    with sqlite3.connect(runtime / DATABASE_FILENAME) as connection:
        connection.execute("UPDATE physical_attempts SET state = 'succeeded' WHERE physical_attempt_id = 'attempt-1'")
    candidate = TemporaryRawCandidate("attempt-1", BODY, HASH, "application/json", "utf-8")
    intent = RawPublicationIntent(
        publication_id="publication-1",
        task_id="task-1",
        logical_request_id="logical-1",
        physical_attempt_id="attempt-1",
        source_id="fixture",
        operation="history",
        content_sha256=HASH,
        byte_count=len(BODY),
        media_type="application/json",
        encoding="utf-8",
        raw_schema_id="fixture-raw",
        raw_schema_version=1,
        publication_generation=1,
    )
    return repository, lease, runs, candidate, intent


def test_publish_exact_raw_bundle_after_source_validation(tmp_path: Path) -> None:
    """Publish exact bytes only after the source-native validator accepts them."""
    repository, lease, runs, candidate, intent = _prepared(tmp_path)
    observed: list[bytes] = []

    reference = RawArtifactPublisher(runs, repository).publish(
        candidate, intent, lease, NOW + timedelta(seconds=2), NOW + timedelta(seconds=3), observed.append
    )

    bundle = runs / "task-1" / reference.relative_path
    assert observed == [BODY]
    assert (bundle / "body.bin").read_bytes() == BODY
    assert repository.raw_publication_state("publication-1") == ("committed", reference.relative_path)
    assert not list((runs / "task-1" / ".staging" / "acquisitions").iterdir())


def test_source_validation_failure_never_exposes_bundle(tmp_path: Path) -> None:
    """Keep a rejected candidate out of the committed acquisition path."""
    repository, lease, runs, candidate, intent = _prepared(tmp_path)

    def reject(body: bytes) -> None:
        raise ValueError("invalid fixture payload")

    with pytest.raises(ValueError, match="invalid fixture payload"):
        RawArtifactPublisher(runs, repository).publish(
            candidate, intent, lease, NOW + timedelta(seconds=2), NOW + timedelta(seconds=3), reject
        )

    assert not (runs / "task-1" / "acquisitions" / "logical-1" / "attempt-1").exists()
    assert repository.raw_publication_state("publication-1") == ("failed", None)


def test_candidate_mismatch_fails_before_intent_or_filesystem_write(tmp_path: Path) -> None:
    """Reject inconsistent bytes before creating durable or filesystem state."""
    repository, lease, runs, candidate, intent = _prepared(tmp_path)
    mismatched = TemporaryRawCandidate("attempt-1", b"changed", HASH, "application/json", "utf-8")

    with pytest.raises(RuntimeStorageError, match="raw_candidate_intent_mismatch"):
        RawArtifactPublisher(runs, repository).publish(
            mismatched, intent, lease, NOW + timedelta(seconds=2), NOW + timedelta(seconds=3), lambda body: None
        )

    assert repository.raw_publication_state("publication-1") is None
    assert list(runs.iterdir()) == []
