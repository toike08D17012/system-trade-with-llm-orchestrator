"""Publish immutable internal preparation files with atomic visibility."""

import os
import re
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path, PurePosixPath

from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.task import DetailedAnalysisTaskV1


PreparationValidator = Callable[[Mapping[str, bytes]], None]
_DESTINATION_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_STAGING_PREFIX = ".staging-"


@dataclass(frozen=True, slots=True)
class StoredPreparationFile:
    """Hash receipt for one exact file in a published preparation."""

    relative_path: str
    sha256: str


@dataclass(frozen=True, slots=True)
class PreparationReceipt:
    """Internal receipt for a completely published preparation directory."""

    destination_name: str
    files: tuple[StoredPreparationFile, ...]


def publish_preparation(
    root: Path,
    destination_name: str,
    files: Mapping[str, bytes],
    *,
    validator: PreparationValidator,
) -> PreparationReceipt:
    """Validate and atomically expose a complete internal preparation.

    The caller must exclusively own a trusted root. Concurrent threads, processes,
    and external filesystem mutation are outside this function's guarantees. Atomic
    visibility relies on staging and destination residing on the same filesystem.

    Args:
        root: Existing trusted directory dedicated to internal preparations.
        destination_name: Unique completed-directory name supplied by the caller.
        files: Exact bytes keyed by safe POSIX-style relative paths.
        validator: Validation performed against bytes read back from staging.

    Returns:
        A receipt prepared before the atomic publication step.

    Raises:
        FileExistsError: If the completed destination already exists.
        ValueError: If names, paths, bytes, or the trusted root are invalid.
        OSError: If filesystem operations fail.
    """
    _validate_root(root)
    _validate_destination_name(destination_name)
    normalized_files = _validate_files(files)
    destination = root / destination_name
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"preparation destination already exists: {destination_name}")

    staging = Path(tempfile.mkdtemp(prefix=_STAGING_PREFIX, dir=root))
    published = False
    try:
        for relative_path, content in normalized_files.items():
            output_path = staging.joinpath(*PurePosixPath(relative_path).parts)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(content)

        observed = {relative_path: (staging / relative_path).read_bytes() for relative_path in normalized_files}
        if observed != normalized_files:
            raise OSError("preparation bytes changed while staging")
        validator(observed)
        receipt = PreparationReceipt(
            destination_name=destination_name,
            files=tuple(
                StoredPreparationFile(relative_path=relative_path, sha256=sha256(content).hexdigest())
                for relative_path, content in observed.items()
            ),
        )

        if destination.exists() or destination.is_symlink():
            raise FileExistsError(f"preparation destination already exists: {destination_name}")
        os.rename(staging, destination)
        published = True
        return receipt
    finally:
        if not published:
            _remove_staging_tree(staging)


def publish_task_preparation(root: Path, destination_name: str, task: DetailedAnalysisTaskV1) -> PreparationReceipt:
    """Serialize, validate, and publish one task as an internal preparation."""
    task_bytes = task.model_dump_json(indent=2).encode("utf-8") + b"\n"

    def validate_task(files: Mapping[str, bytes]) -> None:
        DetailedAnalysisTaskV1.model_validate_json(files["task.json"])

    return publish_preparation(root, destination_name, {"task.json": task_bytes}, validator=validate_task)


def _validate_root(root: Path) -> None:
    if root.is_symlink() or not root.is_dir():
        raise ValueError("preparation root must be an existing non-symlink directory")


def _validate_destination_name(destination_name: str) -> None:
    if not _DESTINATION_NAME_PATTERN.fullmatch(destination_name) or destination_name.startswith(_STAGING_PREFIX):
        raise ValueError("destination name must match [A-Za-z0-9][A-Za-z0-9_-]{0,63}")


def _validate_files(files: Mapping[str, bytes]) -> dict[str, bytes]:
    if not files:
        raise ValueError("at least one preparation file is required")
    normalized: dict[str, bytes] = {}
    file_paths: set[PurePosixPath] = set()
    for relative_path, content in files.items():
        path = PurePosixPath(relative_path)
        if (
            not relative_path
            or "\\" in relative_path
            or path.is_absolute()
            or any(part in {"", ".", ".."} for part in path.parts)
            or relative_path != path.as_posix()
        ):
            raise ValueError("preparation file paths must be normalized relative POSIX paths")
        if not isinstance(content, bytes):
            raise ValueError("preparation file content must be bytes")
        if path in file_paths or any(path in other.parents or other in path.parents for other in file_paths):
            raise ValueError("preparation file paths must be unique and cannot conflict with directories")
        file_paths.add(path)
        normalized[relative_path] = content
    return normalized


def _remove_staging_tree(staging: Path) -> None:
    """Remove only the unpublished staging tree created by this invocation."""
    for current_root, directory_names, file_names in os.walk(staging, topdown=False):
        current = Path(current_root)
        for file_name in file_names:
            (current / file_name).unlink()
        for directory_name in directory_names:
            (current / directory_name).rmdir()
    staging.rmdir()
