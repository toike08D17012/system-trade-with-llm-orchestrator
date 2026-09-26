"""Policy acceptance, Decimal integrity, retained evidence, and offline CLI tests."""

import csv
import io
import json
import os
import shutil
import socket
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

import pytest
import yaml

from stock_research_llm_orchestrator.preparation.price_acceptance import (
    AcceptanceCondition,
    KnownPriceConflict,
    PriceAcceptanceIndex,
    accept_price_evidence,
    acceptance_status,
    validate_price_acceptance,
)
from stock_research_llm_orchestrator.preparation.price_acceptance_cli import main
from stock_research_llm_orchestrator.sources.yfinance.normalization import COLUMNS, normalize_price_row

from .test_market_evidence import NOW
from .test_market_revalidation import RevalidationScenario, _bundle


class AcceptanceScenario(RevalidationScenario):
    """Use complete synthetic retained evidence without touching production sources."""

    def accept(self, *, conflict: bool = False, approval_root: Path | None = None) -> PriceAcceptanceIndex:
        """Exercise the public API with deterministic time."""
        return accept_price_evidence(
            preparation=self.root / "prepared",
            calendar=self.root / "calendar.json",
            calendar_metadata=self.root / "metadata.json",
            research_note=self.root / "note.md",
            output=self.root / "accepted",
            checked_at=NOW + timedelta(seconds=1),
            approval_root=approval_root or Path("config/source-approvals"),
            known_conflict=self.root / "conflict.json" if conflict else None,
            conflict_evidence=self.root / "conflict.txt" if conflict else None,
        )

    def edit_prices(self, fault: str) -> None:
        """Retain altered source and matching normalized values to test semantic validation."""
        folder = self.root / "prepared"
        rows = list(csv.reader(io.StringIO((folder / "prices.csv").read_text())))
        header, data = rows[0], rows[1:]
        if fault == "duplicate":
            data[1][0] = data[0][0]
        elif fault == "missing":
            data.pop(2)
        elif fault == "extra":
            row = data[0].copy()
            row[0] = "2023-09-30 00:00:00+09:00"
            data.append(row)
            data.sort(key=lambda row: row[0])
        elif fault == "empty":
            data = []
        else:
            column, value = {
                "negative": ("Close", "-1"),
                "null": ("Close", ""),
                "nonfinite": ("High", "inf"),
                "fractional": ("Volume", "1.5"),
                "ohlc": ("Low", "999999"),
                "precision": ("Adj Close", "101.12345678901234567890123456789"),
            }[fault]
            data[0][header.index(column)] = value
        stream = io.StringIO()
        csv.writer(stream, lineterminator="\n").writerows([header, *data])
        (folder / "prices.csv").write_text(stream.getvalue())
        normalized = json.loads((folder / "normalized.json").read_bytes())
        normalized["rows"] = [
            normalize_price_row(
                datetime.fromisoformat(row[0]).date().isoformat(),
                tuple(row[header.index(column)] or None for column in COLUMNS),
                [],
            ).model_dump(mode="json")
            for row in data
        ]
        (folder / "normalized.json").write_text(json.dumps(normalized))
        _rehash(folder)


def _rehash(folder: Path) -> None:
    index = json.loads((folder / "index.json").read_bytes())
    for reference in index["files"]:
        reference["sha256"] = sha256((folder / reference["relative_path"]).read_bytes()).hexdigest()
    index["normalized_evidence"]["data_version"] = sha256((folder / "prices.csv").read_bytes()).hexdigest()
    index["normalized_evidence"]["value"] = json.loads((folder / "normalized.json").read_bytes())
    index["normalized_evidence"]["content_sha256"] = sha256((folder / "normalized.json").read_bytes()).hexdigest()
    (folder / "index.json").write_text(json.dumps(index))


@pytest.fixture(autouse=True)
def _deny_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def reject(*args: object, **kwargs: object) -> None:
        pytest.fail("price acceptance attempted network I/O")

    monkeypatch.setattr(socket.socket, "connect", reject)
    monkeypatch.setattr(socket, "getaddrinfo", reject)


def test_acceptance_keeps_limits_originals_and_reproducible_receipt(tmp_path: Path) -> None:
    """Accept valid prices despite permitted gaps, without granting analysis readiness."""
    scenario = AcceptanceScenario(tmp_path)
    before = _bundle(tmp_path / "prepared")
    result = scenario.accept()
    assert result.status == "accepted_with_limitations"
    assert all(condition.passed for condition in result.conditions)
    assert not result.analysis_ready
    assert result.original_issues
    assert "dividend_currency_not_independently_verified" in result.limitations
    assert any("total-return" in restriction for restriction in result.restricted_uses)
    retained = _bundle(tmp_path / "accepted")
    validate_price_acceptance(retained)
    assert _bundle(tmp_path / "prepared") == before
    assert all(retained[f"preparation/{name}"] == body for name, body in before.items())
    assert all(path in retained for condition in result.conditions for path in condition.evidence_paths)
    (tmp_path / "note.md").unlink()
    validate_price_acceptance(retained)


@pytest.mark.parametrize(
    "fault,condition",
    [
        ("duplicate", "PA-04"),
        ("missing", "PA-04"),
        ("extra", "PA-04"),
        ("empty", "PA-03"),
        ("negative", "PA-05"),
        ("null", "PA-05"),
        ("nonfinite", "PA-05"),
        ("fractional", "PA-05"),
        ("ohlc", "PA-05"),
    ],
)
def test_invalid_source_values_are_pending_even_with_consistent_hashes(
    tmp_path: Path, fault: str, condition: str
) -> None:
    """Recalculate policy results instead of trusting retained quality booleans."""
    scenario = AcceptanceScenario(tmp_path)
    scenario.edit_prices(fault)
    result = scenario.accept()
    assert result.status == "pending"
    assert not next(item for item in result.conditions if item.condition_id == condition).passed
    validate_price_acceptance(_bundle(tmp_path / "accepted"))


def test_precision_is_preserved_and_inconsistent_normalized_value_is_rejected(tmp_path: Path) -> None:
    """High precision source strings survive, while a rehashed value change fails."""
    scenario = AcceptanceScenario(tmp_path)
    scenario.edit_prices("precision")
    assert scenario.accept().status == "accepted_with_limitations"
    folder = tmp_path / "prepared"
    normalized = json.loads((folder / "normalized.json").read_bytes())
    normalized["rows"][0]["adjusted_close"] = "101.12345678901234567890123456788"
    (folder / "normalized.json").write_text(json.dumps(normalized))
    _rehash(folder)
    shutil.rmtree(tmp_path / "accepted")
    with pytest.raises(ValueError, match="source_normalized_values_mismatch"):
        scenario.accept()
    assert not (tmp_path / "accepted").exists()


@pytest.mark.parametrize(
    "field,value,condition",
    [
        ("response_symbol", "9999.T", "PA-01"),
        ("currency", None, "PA-06"),
        ("exchange_timezone", "UTC", "PA-06"),
        ("library_version", "0.0", "PA-02"),
    ],
)
def test_metadata_mismatch_blocks_acceptance(tmp_path: Path, field: str, value: str | None, condition: str) -> None:
    """Source metadata is evaluated independently of the old verified flag."""
    scenario = AcceptanceScenario(tmp_path)
    folder = tmp_path / "prepared"
    metadata = json.loads((folder / "history-metadata.json").read_bytes())
    metadata[field] = value
    (folder / "history-metadata.json").write_text(json.dumps(metadata))
    _rehash(folder)
    result = scenario.accept()
    assert result.status == "pending"
    assert not next(item for item in result.conditions if item.condition_id == condition).passed


def test_short_calendar_is_pending_not_technical_failure(tmp_path: Path) -> None:
    """A valid but insufficient calendar retains an actionable incomplete result."""
    scenario = AcceptanceScenario(tmp_path)
    calendar = json.loads(scenario.calendar)
    calendar["period"]["start_date"] = "2023-09-27"
    calendar["dates"].pop(0)
    scenario.update_calendar(json.dumps(calendar).encode())
    result = scenario.accept()
    assert result.status == "pending"
    assert not result.conditions[2].passed
    assert not result.conditions[3].passed


def test_known_conflict_is_bound_to_evidence_and_blocks_acceptance(tmp_path: Path) -> None:
    """Known delisting must not be treated as the permitted unknown-listing gap."""
    scenario = AcceptanceScenario(tmp_path)
    body = b"Synthetic documented delisting"
    (tmp_path / "conflict.txt").write_bytes(body)
    conflict = KnownPriceConflict(
        evidence_id="conflict-1",
        source_reference="fixture://delisting",
        checked_on="2026-09-26",
        security_code="7203",
        category="delisted",
        evidence_sha256=sha256(body).hexdigest(),
    )
    (tmp_path / "conflict.json").write_text(conflict.model_dump_json())
    result = scenario.accept(conflict=True)
    assert result.status == "pending"
    assert result.conditions[-1].reasons == ("known_conflict:delisted",)
    validate_price_acceptance(_bundle(tmp_path / "accepted"))


def test_expired_approval_is_not_reused_as_current_permission(tmp_path: Path) -> None:
    """Retain and check the applicable approval document, not only its numeric version."""
    scenario = AcceptanceScenario(tmp_path)
    approvals = tmp_path / "approvals"
    shutil.copytree("config/source-approvals", approvals)
    path = approvals / "yfinance/v3.yaml"
    approval = yaml.safe_load(path.read_bytes())
    approval["recheck_due_on"] = "2026-09-25"
    path.write_text(yaml.safe_dump(approval))
    result = scenario.accept(approval_root=approvals)
    assert result.status == "pending"
    assert not result.conditions[1].passed


def test_unknown_original_issue_is_not_silently_waived(tmp_path: Path) -> None:
    """The allowlist cannot clear future checks it does not understand."""
    scenario = AcceptanceScenario(tmp_path)
    path = tmp_path / "prepared/index.json"
    index = json.loads(path.read_bytes())
    index["issues"].append({"code": "new_unrecognized_check", "on": None, "field": None})
    path.write_text(json.dumps(index))
    assert scenario.accept().status == "pending"


@pytest.mark.parametrize("fault", ["hash", "symlink", "overlap", "traversal", "publication"])
def test_technical_failures_publish_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str) -> None:
    """Refuse unsafe input/output and clean incomplete staging on write failure."""
    scenario = AcceptanceScenario(tmp_path)
    if fault == "hash":
        (tmp_path / "prepared/prices.csv").write_bytes(b"corrupt")
    elif fault == "symlink":
        (tmp_path / "calendar.json").unlink()
        (tmp_path / "calendar.json").symlink_to(tmp_path / "prepared/prices.csv")
    elif fault == "overlap":
        with pytest.raises(ValueError, match="overlapping"):
            accept_price_evidence(
                preparation=tmp_path / "prepared",
                calendar=tmp_path / "calendar.json",
                calendar_metadata=tmp_path / "metadata.json",
                research_note=tmp_path / "note.md",
                output=tmp_path / "prepared/output",
                checked_at=NOW,
            )
        return
    elif fault == "traversal":
        scenario.root = tmp_path / "x/.."
    else:

        def fail(*args: object) -> None:
            raise OSError("synthetic publication failure")

        monkeypatch.setattr(os, "rename", fail)
    with pytest.raises((ValueError, OSError)):
        scenario.accept()
    assert not (tmp_path / "accepted").exists()
    assert not list(tmp_path.glob(".staging-*"))


def test_receipt_tampering_and_overwrite_are_rejected(tmp_path: Path) -> None:
    """Keep stored status, conditions, restrictions, and bytes reproducibly bound."""
    scenario = AcceptanceScenario(tmp_path)
    scenario.accept()
    before = _bundle(tmp_path / "accepted")
    with pytest.raises(FileExistsError):
        scenario.accept()
    assert _bundle(tmp_path / "accepted") == before
    index = json.loads(before["index.json"])
    index["status"] = "accepted"
    with pytest.raises(ValueError, match="receipt_mismatch"):
        validate_price_acceptance({**before, "index.json": json.dumps(index).encode()})


def test_status_aggregation_requires_all_conditions_and_preserves_limits() -> None:
    """Exercise the policy branch not reachable from the currently limited native source."""
    conditions = tuple(
        AcceptanceCondition(condition_id=f"PA-{number:02d}", passed=True, reasons=(), evidence_paths=())
        for number in range(1, 10)
    )
    assert acceptance_status(conditions, ()) == "accepted"
    assert acceptance_status(conditions, ("known_limit",)) == "accepted_with_limitations"
    with pytest.raises(ValueError):
        acceptance_status(conditions[:-1], ())


def test_cli_exit_codes_distinguish_acceptance_pending_and_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Check actual CLI publication with a deterministic clock and no network."""
    from stock_research_llm_orchestrator.preparation import price_acceptance_cli

    class Clock(datetime):
        @classmethod
        def now(cls, tz: object = None) -> Clock:
            return cls.fromtimestamp((NOW + timedelta(seconds=1)).timestamp(), UTC)

    monkeypatch.setattr(price_acceptance_cli, "datetime", Clock)
    scenario = AcceptanceScenario(tmp_path)
    arguments = []
    for option, name in (
        ("preparation", "prepared"),
        ("calendar", "calendar.json"),
        ("calendar-metadata", "metadata.json"),
        ("research-note", "note.md"),
        ("output", "accepted"),
    ):
        arguments.extend([f"--{option}", str(tmp_path / name)])
    assert main(arguments) == 0
    assert json.loads(capsys.readouterr().out)["analysis_ready"] is False
    assert main(arguments) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "failed"
    scenario.edit_prices("null")
    arguments[-1] = str(tmp_path / "pending")
    assert main(arguments) == 2
    assert "PA-05" in json.loads(capsys.readouterr().out)["failed_conditions"]


def test_short_history_is_pending_even_when_all_requested_days_exist(tmp_path: Path) -> None:
    """An internally consistent one-year slice does not satisfy three-year acceptance."""
    scenario = AcceptanceScenario(tmp_path)
    folder = tmp_path / "prepared"
    normalized = json.loads((folder / "normalized.json").read_bytes())
    normalized["requested_period"]["start_date"] = "2025-09-26"
    normalized["rows"] = [row for row in normalized["rows"] if row["on"] >= "2025-09-26"]
    normalized["observed_period"]["start_date"] = normalized["rows"][0]["on"]
    normalized["three_year_request"] = False
    (folder / "normalized.json").write_text(json.dumps(normalized))
    rows = list(csv.reader(io.StringIO((folder / "prices.csv").read_text())))
    stream = io.StringIO()
    csv.writer(stream, lineterminator="\n").writerows([rows[0], *[row for row in rows[1:] if row[0] >= "2025-09-26"]])
    (folder / "prices.csv").write_text(stream.getvalue())
    metadata = json.loads((folder / "history-metadata.json").read_bytes())
    metadata["arguments"]["start"] = "2025-09-26"
    (folder / "history-metadata.json").write_text(json.dumps(metadata))
    _rehash(folder)
    result = scenario.accept()
    assert result.status == "pending"
    assert not result.conditions[2].passed
    assert result.conditions[3].passed
