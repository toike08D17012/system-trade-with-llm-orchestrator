"""Crash-aware publication of immutable raw acquisition bundles."""

import hashlib
import json
import os
import stat
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from stock_research_llm_orchestrator.requests.production import (
    CommittedRawReference,
    RawPublicationIntent,
    RuntimeLease,
)
from stock_research_llm_orchestrator.requests.storage import ProductionRequestRepository, RuntimeStorageError
from stock_research_llm_orchestrator.requests.transport import TemporaryRawCandidate


SourceNativeValidator = Callable[[bytes], None]


class RawArtifactPublisher:
    """Stage, validate, atomically expose, and durably reference exact bytes."""

    def __init__(self, runs_root: Path, repository: ProductionRequestRepository) -> None:
        """Bind one trusted runs root to the production repository."""
        self._runs_root = runs_root
        self._repository = repository

    def publish(
        self,
        candidate: TemporaryRawCandidate,
        intent: RawPublicationIntent,
        lease: RuntimeLease,
        started_at: datetime,
        committed_at: datetime,
        source_validator: SourceNativeValidator,
    ) -> CommittedRawReference:
        """Publish only when exact bytes, metadata, and source-native parsing agree."""
        intent = RawPublicationIntent.model_validate(intent.model_dump(warnings=False))
        if (
            candidate.physical_attempt_id != intent.physical_attempt_id
            or candidate.sha256 != intent.content_sha256
            or len(candidate.body) != intent.byte_count
            or candidate.media_type != intent.media_type
            or candidate.encoding != intent.encoding
        ):
            raise RuntimeStorageError("raw_candidate_intent_mismatch")
        _validate_runs_root(self._runs_root)
        self._repository.begin_raw_publication(intent, lease, started_at)

        task_root = self._runs_root / intent.task_id
        task_root.mkdir(mode=0o700, exist_ok=True)
        _validate_private_directory(task_root)
        staging_parent = task_root / ".staging" / "acquisitions"
        staging_parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        staging = staging_parent / intent.physical_attempt_id
        destination_parent = task_root / "acquisitions" / intent.logical_request_id
        destination = destination_parent / intent.physical_attempt_id
        if staging.exists() or staging.is_symlink() or destination.exists() or destination.is_symlink():
            raise RuntimeStorageError("raw_publication_path_exists")

        staging.mkdir(mode=0o700)
        renamed = False
        try:
            request_metadata = {
                "logical_request_id": intent.logical_request_id,
                "operation": intent.operation,
                "physical_attempt_id": intent.physical_attempt_id,
                "publication_generation": intent.publication_generation,
                "publication_id": intent.publication_id,
                "source_id": intent.source_id,
                "task_id": intent.task_id,
            }
            response_metadata = {
                "byte_count": intent.byte_count,
                "encoding": intent.encoding,
                "media_type": intent.media_type,
                "raw_schema_id": intent.raw_schema_id,
                "raw_schema_version": intent.raw_schema_version,
            }
            receipt = {"body_sha256": intent.content_sha256, "byte_count": intent.byte_count}
            _write_exact(staging / "body.bin", candidate.body)
            _write_exact(staging / "request.json", _json_bytes(request_metadata))
            _write_exact(staging / "response.json", _json_bytes(response_metadata))
            _write_exact(staging / "receipt.json", _json_bytes(receipt))
            source_validator((staging / "body.bin").read_bytes())
            _verify_staged_bundle(staging, intent)
            _fsync_directory(staging)
            destination_parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.rename(staging, destination)
            renamed = True
            _fsync_directory(destination_parent)
        except Exception as error:
            if renamed:
                raise RuntimeStorageError("raw_publication_reconciliation_required") from error
            self._repository.fail_raw_publication(intent.publication_id, lease, committed_at)
            raise
        finally:
            if not renamed and staging.is_dir() and not staging.is_symlink():
                _remove_bundle(staging)

        relative_path = f"acquisitions/{intent.logical_request_id}/{intent.physical_attempt_id}"
        reference = CommittedRawReference(
            publication_id=intent.publication_id,
            task_id=intent.task_id,
            logical_request_id=intent.logical_request_id,
            physical_attempt_id=intent.physical_attempt_id,
            relative_path=relative_path,
            content_sha256=intent.content_sha256,
            byte_count=intent.byte_count,
            raw_schema_id=intent.raw_schema_id,
            raw_schema_version=intent.raw_schema_version,
            publication_generation=intent.publication_generation,
        )
        try:
            self._repository.commit_raw_publication(reference, lease, committed_at)
        except Exception as error:
            raise RuntimeStorageError("raw_publication_reconciliation_required") from error
        return reference


def _validate_runs_root(root: Path) -> None:
    if root.is_symlink() or not root.is_dir():
        raise RuntimeStorageError("invalid_runs_root")
    _validate_private_directory(root)


def _validate_private_directory(path: Path) -> None:
    details = path.stat()
    if details.st_uid != os.geteuid() or stat.S_IMODE(details.st_mode) != 0o700:
        raise RuntimeStorageError("invalid_artifact_directory_permissions")


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"


def _write_exact(path: Path, content: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(descriptor)


def _verify_staged_bundle(staging: Path, intent: RawPublicationIntent) -> None:
    body = (staging / "body.bin").read_bytes()
    receipt = json.loads((staging / "receipt.json").read_bytes())
    if (
        len(body) != intent.byte_count
        or hashlib.sha256(body).hexdigest() != intent.content_sha256
        or receipt != {"body_sha256": intent.content_sha256, "byte_count": intent.byte_count}
    ):
        raise RuntimeStorageError("raw_staging_verification_failed")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _remove_bundle(path: Path) -> None:
    for child in path.iterdir():
        child.unlink()
    path.rmdir()
