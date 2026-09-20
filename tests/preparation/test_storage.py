"""Tests for atomic internal preparation publication."""

import os
from hashlib import sha256
from pathlib import Path

import pytest

from stock_research_llm_orchestrator.preparation.storage import publish_preparation


def test_publish_preparation_exposes_complete_validated_bytes(tmp_path: Path) -> None:
    """Publish all files at once after validation and return exact hashes."""
    observed_destination_states: list[bool] = []

    def validate(files: object) -> None:
        observed_destination_states.append((tmp_path / "prep-001").exists())
        assert files == {"nested/source.bin": b"source", "task.json": b"{}"}

    receipt = publish_preparation(
        tmp_path,
        "prep-001",
        {"nested/source.bin": b"source", "task.json": b"{}"},
        validator=validate,
    )

    assert observed_destination_states == [False]
    assert (tmp_path / "prep-001" / "nested" / "source.bin").read_bytes() == b"source"
    assert (tmp_path / "prep-001" / "task.json").read_bytes() == b"{}"
    assert receipt.destination_name == "prep-001"
    assert {item.relative_path: item.sha256 for item in receipt.files} == {
        "nested/source.bin": sha256(b"source").hexdigest(),
        "task.json": sha256(b"{}").hexdigest(),
    }
    assert not list(tmp_path.glob(".staging-*"))


def test_publish_preparation_cleans_staging_after_validation_failure(tmp_path: Path) -> None:
    """Leave no completed output when validation fails before publication."""

    def reject(files: object) -> None:
        del files
        raise ValueError("invalid fixture")

    with pytest.raises(ValueError, match="invalid fixture"):
        publish_preparation(tmp_path, "prep-001", {"task.json": b"{}"}, validator=reject)

    assert not (tmp_path / "prep-001").exists()
    assert not list(tmp_path.iterdir())


def test_publish_preparation_preserves_existing_destination(tmp_path: Path) -> None:
    """Reject collisions without changing existing completed bytes."""
    destination = tmp_path / "prep-001"
    destination.mkdir()
    (destination / "existing.txt").write_bytes(b"keep")

    with pytest.raises(FileExistsError):
        publish_preparation(tmp_path, "prep-001", {"task.json": b"new"}, validator=lambda files: None)

    assert (destination / "existing.txt").read_bytes() == b"keep"
    assert list(destination.iterdir()) == [destination / "existing.txt"]


@pytest.mark.parametrize(
    "relative_path",
    ["", "/absolute", "../outside", "nested/../outside", "nested\\outside", "./task.json", "a//b"],
)
def test_publish_preparation_rejects_unsafe_paths(tmp_path: Path, relative_path: str) -> None:
    """Reject paths that are not normalized beneath the trusted root."""
    with pytest.raises(ValueError, match="normalized relative POSIX"):
        publish_preparation(tmp_path, "prep-001", {relative_path: b"data"}, validator=lambda files: None)

    assert not list(tmp_path.iterdir())


def test_publish_preparation_rejects_file_directory_conflicts(tmp_path: Path) -> None:
    """Reject a file path that is also needed as a directory."""
    with pytest.raises(ValueError, match="conflict"):
        publish_preparation(
            tmp_path,
            "prep-001",
            {"evidence": b"file", "evidence/raw.json": b"nested"},
            validator=lambda files: None,
        )


def test_publish_preparation_rejects_symlink_root(tmp_path: Path) -> None:
    """Reject an untrusted root reached through a symlink."""
    actual_root = tmp_path / "actual"
    actual_root.mkdir()
    linked_root = tmp_path / "linked"
    linked_root.symlink_to(actual_root, target_is_directory=True)

    with pytest.raises(ValueError, match="non-symlink"):
        publish_preparation(linked_root, "prep-001", {"task.json": b"{}"}, validator=lambda files: None)

    assert not list(actual_root.iterdir())


def test_publish_preparation_cleans_staging_when_rename_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Do not adopt staged data after the publication operation fails."""

    def fail_rename(source: Path, destination: Path) -> None:
        del source, destination
        raise OSError("rename failed")

    monkeypatch.setattr(os, "rename", fail_rename)
    with pytest.raises(OSError, match="rename failed"):
        publish_preparation(tmp_path, "prep-001", {"task.json": b"{}"}, validator=lambda files: None)

    assert not list(tmp_path.iterdir())
