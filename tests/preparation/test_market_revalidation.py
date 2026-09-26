"""Offline receipt integrity, truthful partial findings, and immutable inputs."""

import json
import os
import socket
from datetime import timedelta
from hashlib import sha256
from pathlib import Path

import pytest

from stock_research_llm_orchestrator.preparation.market_revalidation import (
    MarketRevalidationIndex,
    PublicationObservation,
    ResearchCalendarMetadata,
    revalidate_market_evidence,
    validate_market_revalidation,
)
from stock_research_llm_orchestrator.preparation.market_revalidation_cli import main

from .test_market_evidence import NOW, Scenario


class RevalidationScenario:
    """Retain real synthetic preparations and research inputs for each test."""

    def __init__(self, root: Path) -> None:
        """Build a complete source-bound preparation using the existing offline fixture."""
        scenario = Scenario(root)
        scenario.prepare()
        self.root = root
        self.calendar = scenario.calendar().body
        (root / "calendar.json").write_bytes(self.calendar)
        (root / "note.md").write_bytes(b"Synthetic research note, not runtime approval.\n")
        self.note_hash = sha256((root / "note.md").read_bytes()).hexdigest()
        self.update_calendar(self.calendar)
        observation = PublicationObservation(
            evidence_id="observation-1",
            source_reference="https://www.jpx.co.jp/markets/statistics-equities/misc/01.html",
            observed_on="2026-09-26",
            recorded_at=NOW.isoformat(),
            latest_published_month="2026-08",
            research_note_sha256=self.note_hash,
        )
        (root / "observation.json").write_text(observation.model_dump_json())

    def update_calendar(self, body: bytes) -> None:
        """Bind an explicitly supplied synthetic calendar to its matching metadata."""
        (self.root / "calendar.json").write_bytes(body)
        metadata = ResearchCalendarMetadata(
            evidence_id="calendar-1",
            checked_at=NOW.isoformat(),
            calendar_sha256=sha256(body).hexdigest(),
            research_note_sha256=self.note_hash,
            source_references=("fixture://synthetic-calendar",),
            rules=("synthetic weekdays",),
        )
        (self.root / "metadata.json").write_text(metadata.model_dump_json())

    def run(self, *, observation: bool = True, output: Path | None = None) -> MarketRevalidationIndex:
        """Run the public offline API with deterministic time and separate output."""
        return revalidate_market_evidence(
            preparation=self.root / "prepared",
            calendar=self.root / "calendar.json",
            calendar_metadata=self.root / "metadata.json",
            research_note=self.root / "note.md",
            publication_observation=self.root / "observation.json" if observation else None,
            output=output if output is not None else self.root / "revalidated",
            checked_at=NOW + timedelta(seconds=1),
        )


def _bundle(path: Path) -> dict[str, bytes]:
    return {str(file.relative_to(path)): file.read_bytes() for file in path.rglob("*") if file.is_file()}


@pytest.fixture(autouse=True)
def _deny_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def reject(*args: object, **kwargs: object) -> None:
        pytest.fail("offline revalidation attempted network I/O")

    monkeypatch.setattr(socket.socket, "connect", reject)
    monkeypatch.setattr(socket, "getaddrinfo", reject)


def test_complete_receipt_reproduces_findings_and_preserves_inputs(tmp_path: Path) -> None:
    """Date matching does not clear original gaps or grant source approval."""
    scenario = RevalidationScenario(tmp_path)
    before = _bundle(tmp_path / "prepared")
    index = scenario.run()
    assert index.findings.scheduled_dates_match
    assert index.findings.latest_published_month_match == "matched"
    assert index.findings.current_listing_eligibility == "unconfirmed"
    assert {issue.code for issue in index.findings.original_issues} == {
        "trading_dates_unconfirmed",
        "jpx_snapshot_freshness_unconfirmed",
    }
    assert not index.analysis_ready
    assert not index.findings.original_price_quality_passed
    assert index.source_approval == "research_only"
    assert index.findings.original_index_sha256 == sha256(before["index.json"]).hexdigest()
    assert _bundle(tmp_path / "prepared") == before
    retained = _bundle(tmp_path / "revalidated")
    validate_market_revalidation(retained)
    assert all(retained[f"preparation/{name}"] == body for name, body in before.items())
    # Revalidation remains reproducible after original source files are unavailable.
    (tmp_path / "note.md").unlink()
    validate_market_revalidation(retained)


def test_date_differences_are_explicit(tmp_path: Path) -> None:
    """Compare date sets, preserving both missing and unexpected observations."""
    scenario = RevalidationScenario(tmp_path)
    calendar = json.loads(scenario.calendar)
    removed = calendar["dates"].pop(0)
    added = "2023-09-30"
    calendar["dates"] = sorted([*calendar["dates"], added])
    scenario.update_calendar(json.dumps(calendar).encode())
    findings = scenario.run().findings
    assert not findings.scheduled_dates_match
    assert findings.missing_dates == (added,)
    assert findings.unexpected_dates == (removed,)


@pytest.mark.parametrize("month,expected", [(None, "unconfirmed"), ("2026-07", "mismatched")])
def test_observation_absence_or_mismatch_is_not_success(tmp_path: Path, month: str | None, expected: str) -> None:
    """Retain useful calendar findings when monthly observation cannot match."""
    scenario = RevalidationScenario(tmp_path)
    observation = json.loads((tmp_path / "observation.json").read_bytes())
    observation["latest_published_month"] = month
    (tmp_path / "observation.json").write_text(json.dumps(observation))
    assert scenario.run(observation=month is not None).findings.latest_published_month_match == expected


@pytest.mark.parametrize(
    "fault", ["hash", "note", "short_period", "duplicate", "invalid_day", "future", "future_observation"]
)
def test_invalid_supplements_publish_nothing(tmp_path: Path, fault: str) -> None:
    """Bad integrity, coverage, dates, or chronology cannot produce a receipt."""
    scenario = RevalidationScenario(tmp_path)
    calendar = json.loads(scenario.calendar)
    if fault in {"hash", "note"}:
        name = "calendar.json" if fault == "hash" else "note.md"
        with (tmp_path / name).open("ab") as stream:
            stream.write(b" ")
    elif fault == "future":
        meta = json.loads((tmp_path / "metadata.json").read_bytes())
        meta["checked_at"] = (NOW + timedelta(days=1)).isoformat()
        (tmp_path / "metadata.json").write_text(json.dumps(meta))
    elif fault == "future_observation":
        observation = json.loads((tmp_path / "observation.json").read_bytes())
        observation["observed_on"] = "2026-09-27"
        (tmp_path / "observation.json").write_text(json.dumps(observation))
    else:
        if fault == "short_period":
            calendar["period"]["start_date"] = "2023-09-27"
            calendar["dates"].pop(0)
        elif fault == "duplicate":
            calendar["dates"].insert(0, calendar["dates"][0])
        else:
            calendar["dates"][0] = "2023-09-00"
        scenario.update_calendar(json.dumps(calendar).encode())
    with pytest.raises(ValueError):
        scenario.run()
    assert not (tmp_path / "revalidated").exists()
    assert not list(tmp_path.glob(".staging-*"))


@pytest.mark.parametrize("fault", ["bytes", "missing", "symlink", "ancestor_symlink", "overlap", "traversal"])
def test_invalid_paths_and_originals_are_rejected(tmp_path: Path, fault: str) -> None:
    """Never follow unsafe references or modify input trees."""
    scenario = RevalidationScenario(tmp_path)
    if fault == "bytes":
        (tmp_path / "prepared/prices.csv").write_bytes(b"corrupt")
    elif fault == "missing":
        (tmp_path / "prepared/prices.csv").unlink()
    elif fault == "symlink":
        (tmp_path / "calendar.json").unlink()
        (tmp_path / "calendar.json").symlink_to(tmp_path / "prepared/prices.csv")
    elif fault == "ancestor_symlink":
        (tmp_path / "alias").symlink_to(tmp_path, target_is_directory=True)
        scenario.root = tmp_path / "alias"
    elif fault == "traversal":
        scenario.root = tmp_path / "x/.."
    else:
        with pytest.raises(ValueError, match="overlapping"):
            scenario.run(output=tmp_path / "prepared/revalidated")
        assert not (tmp_path / "prepared/revalidated").exists()
        return
    with pytest.raises((ValueError, KeyError)):
        scenario.run()
    assert not (tmp_path / "revalidated").exists()


def test_existing_output_and_failed_publication_preserve_originals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both failed staging and repeated runs leave the retained preparation unchanged."""
    scenario = RevalidationScenario(tmp_path)
    before = _bundle(tmp_path / "prepared")
    original = os.rename

    def fail(source: object, destination: object) -> None:
        raise OSError("synthetic publication failure")

    monkeypatch.setattr(os, "rename", fail)
    with pytest.raises(OSError):
        scenario.run()
    assert not list(tmp_path.glob(".staging-*"))
    monkeypatch.setattr(os, "rename", original)
    scenario.run()
    receipt = _bundle(tmp_path / "revalidated")
    with pytest.raises(FileExistsError):
        scenario.run()
    assert _bundle(tmp_path / "revalidated") == receipt
    assert _bundle(tmp_path / "prepared") == before


def test_receipt_tampering_is_rejected(tmp_path: Path) -> None:
    """Verify both byte references and derived assertions on receipt reload."""
    RevalidationScenario(tmp_path).run()
    files = _bundle(tmp_path / "revalidated")
    changed = {**files, "calendar.json": files["calendar.json"] + b" "}
    with pytest.raises(ValueError, match="hash_mismatch"):
        validate_market_revalidation(changed)
    index = json.loads(files["index.json"])
    index["findings"]["scheduled_dates_match"] = False
    with pytest.raises(ValueError, match="findings_mismatch"):
        validate_market_revalidation({**files, "index.json": json.dumps(index).encode()})


def test_cli_reports_gaps_and_sanitizes_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Exercise real argument parsing, retained bytes, atomic publication, and exit codes."""
    RevalidationScenario(tmp_path)
    arguments = []
    for option, name in (
        ("preparation", "prepared"),
        ("calendar", "calendar.json"),
        ("calendar-metadata", "metadata.json"),
        ("research-note", "note.md"),
        ("publication-observation", "observation.json"),
        ("output", "revalidated"),
    ):
        arguments.extend([f"--{option}", str(tmp_path / name)])
    assert main(arguments) == 2
    assert json.loads(capsys.readouterr().out)["scheduled_dates_match"]
    assert main(arguments) == 1
    assert json.loads(capsys.readouterr().out)["reason"] == "market_revalidation_input_or_publication_invalid"


@pytest.mark.parametrize("fault", ["duplicate", "unordered", "invalid_day"])
def test_rehashed_invalid_observed_dates_are_rejected(tmp_path: Path, fault: str) -> None:
    """Consistent hashes alone cannot certify malformed normalized date sequences."""
    scenario = RevalidationScenario(tmp_path)
    folder = tmp_path / "prepared"
    normalized = json.loads((folder / "normalized.json").read_bytes())
    rows = normalized["rows"]
    if fault == "duplicate":
        rows[1]["on"] = rows[0]["on"]
    elif fault == "unordered":
        rows[0], rows[1] = rows[1], rows[0]
    else:
        rows[0]["on"] = "2023-09-00"
    body = json.dumps(normalized).encode()
    digest = sha256(body).hexdigest()
    index = json.loads((folder / "index.json").read_bytes())
    index["normalized_evidence"]["value"] = normalized
    index["normalized_evidence"]["content_sha256"] = digest
    for reference in index["files"]:
        if reference["relative_path"] == "normalized.json":
            reference["sha256"] = digest
    (folder / "normalized.json").write_bytes(body)
    (folder / "index.json").write_text(json.dumps(index))
    with pytest.raises(ValueError):
        scenario.run()
    assert not (tmp_path / "revalidated").exists()
