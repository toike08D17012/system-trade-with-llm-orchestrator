import hashlib
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from stock_research_llm_orchestrator.requests.production import (
    CommittedRawReference,
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
        connection.execute(
            "UPDATE physical_attempts SET state = 'succeeded', raw_eligible = 1 WHERE physical_attempt_id = 'attempt-1'"
        )
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


def test_publish_known_non_2xx_candidate_from_failed_attempt(tmp_path: Path) -> None:
    """Keep bounded provider-error bytes without reclassifying the attempt as successful."""
    repository, lease, runs, candidate, intent = _prepared(tmp_path)
    with sqlite3.connect(tmp_path / ".runtime" / DATABASE_FILENAME) as connection:
        connection.execute("UPDATE physical_attempts SET state = 'failed' WHERE physical_attempt_id = 'attempt-1'")

    reference = RawArtifactPublisher(runs, repository).publish(
        candidate,
        intent,
        lease,
        NOW + timedelta(seconds=2),
        NOW + timedelta(seconds=3),
        lambda _body: None,
    )

    assert repository.physical_attempt_state("attempt-1") == ("failed", 1)
    assert repository.raw_publication_state("publication-1") == ("committed", reference.relative_path)


def test_failed_attempt_without_validated_response_is_not_raw_eligible(tmp_path: Path) -> None:
    """Reject fabricated raw bytes for a failure that produced no bounded response."""
    repository, lease, runs, candidate, intent = _prepared(tmp_path)
    with sqlite3.connect(tmp_path / ".runtime" / DATABASE_FILENAME) as connection:
        connection.execute(
            "UPDATE physical_attempts SET state = 'failed', raw_eligible = 0 WHERE physical_attempt_id = 'attempt-1'"
        )

    with pytest.raises(RuntimeStorageError, match="raw_publication_attempt_mismatch"):
        RawArtifactPublisher(runs, repository).publish(
            candidate,
            intent,
            lease,
            NOW + timedelta(seconds=2),
            NOW + timedelta(seconds=3),
            lambda _body: None,
        )


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


def test_reconcile_forward_repairs_rename_after_database_commit_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Adopt an exact renamed bundle after the original DB commit result was unknown."""
    repository, lease, runs, candidate, intent = _prepared(tmp_path)
    publisher = RawArtifactPublisher(runs, repository)

    def fail_commit(*args: object, **kwargs: object) -> None:
        raise RuntimeStorageError("injected_commit_failure")

    monkeypatch.setattr(repository, "commit_raw_publication", fail_commit)
    with pytest.raises(RuntimeStorageError, match="raw_publication_reconciliation_required"):
        publisher.publish(
            candidate,
            intent,
            lease,
            NOW + timedelta(seconds=2),
            NOW + timedelta(seconds=3),
            lambda body: None,
        )
    assert repository.raw_publication_state("publication-1") == ("reconciliation_required", None)

    monkeypatch.undo()
    repaired = publisher.reconcile(lease, NOW + timedelta(seconds=4))

    assert len(repaired) == 1
    assert repository.raw_publication_state("publication-1") == (
        "committed",
        "acquisitions/logical-1/attempt-1",
    )


def test_reconcile_accepts_commit_that_succeeded_before_result_became_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify a committed bundle when the caller lost the DB commit acknowledgement."""
    repository, lease, runs, candidate, intent = _prepared(tmp_path)
    publisher = RawArtifactPublisher(runs, repository)
    commit = repository.commit_raw_publication

    def commit_then_fail(reference: CommittedRawReference, active_lease: RuntimeLease, now: datetime) -> None:
        commit(reference, active_lease, now)
        raise RuntimeStorageError("injected_lost_acknowledgement")

    monkeypatch.setattr(repository, "commit_raw_publication", commit_then_fail)
    with pytest.raises(RuntimeStorageError, match="raw_publication_reconciliation_required"):
        publisher.publish(
            candidate,
            intent,
            lease,
            NOW + timedelta(seconds=2),
            NOW + timedelta(seconds=3),
            lambda body: None,
        )
    assert repository.raw_publication_state("publication-1") == (
        "committed",
        "acquisitions/logical-1/attempt-1",
    )

    monkeypatch.undo()
    assert publisher.reconcile(lease, NOW + timedelta(seconds=4)) == ()


def test_committed_replay_is_idempotent_and_resolves_from_logical_result(tmp_path: Path) -> None:
    """Return the immutable committed reference without rewriting its bundle."""
    repository, lease, runs, candidate, intent = _prepared(tmp_path)
    publisher = RawArtifactPublisher(runs, repository)
    first = publisher.publish(
        candidate,
        intent,
        lease,
        NOW + timedelta(seconds=2),
        NOW + timedelta(seconds=3),
        lambda body: None,
    )

    replay = publisher.publish(
        candidate,
        intent,
        lease,
        NOW + timedelta(seconds=4),
        NOW + timedelta(seconds=5),
        lambda body: pytest.fail("replay must not parse or rewrite committed bytes"),
    )

    assert replay == first
    assert repository.committed_raw_references("logical-1") == (first,)


def test_replay_rejects_changed_publication_identity(tmp_path: Path) -> None:
    """Reject reuse of a publication ID with different immutable metadata."""
    repository, lease, runs, candidate, intent = _prepared(tmp_path)
    publisher = RawArtifactPublisher(runs, repository)
    publisher.publish(
        candidate,
        intent,
        lease,
        NOW + timedelta(seconds=2),
        NOW + timedelta(seconds=3),
        lambda body: None,
    )
    changed = intent.model_copy(update={"publication_generation": 2})

    with pytest.raises(RuntimeStorageError, match="raw_publication_replay_mismatch"):
        publisher.publish(
            candidate,
            changed,
            lease,
            NOW + timedelta(seconds=4),
            NOW + timedelta(seconds=5),
            lambda body: None,
        )


def test_reconcile_rejects_pre_rename_staging_as_non_reusable(tmp_path: Path) -> None:
    """Delete a pre-rename crash candidate and persist its failed state."""
    repository, lease, runs, candidate, intent = _prepared(tmp_path)
    repository.begin_raw_publication(intent, lease, NOW + timedelta(seconds=2))
    staging = runs / "task-1" / ".staging" / "acquisitions" / "attempt-1"
    staging.mkdir(mode=0o700, parents=True)
    (staging / "body.bin").write_bytes(candidate.body)

    repaired = RawArtifactPublisher(runs, repository).reconcile(lease, NOW + timedelta(seconds=3))

    assert repaired == ()
    assert not staging.exists()
    assert repository.raw_publication_state("publication-1") == ("failed", None)


def test_reconcile_fails_closed_when_committed_body_is_missing(tmp_path: Path) -> None:
    """Refuse to treat a DB-only committed reference as a valid raw bundle."""
    repository, lease, runs, candidate, intent = _prepared(tmp_path)
    reference = RawArtifactPublisher(runs, repository).publish(
        candidate,
        intent,
        lease,
        NOW + timedelta(seconds=2),
        NOW + timedelta(seconds=3),
        lambda body: None,
    )
    (runs / "task-1" / reference.relative_path / "body.bin").unlink()

    with pytest.raises(RuntimeStorageError, match="raw_committed_artifact_invalid"):
        RawArtifactPublisher(runs, repository).reconcile(lease, NOW + timedelta(seconds=4))
