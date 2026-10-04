"""Offline native JPX-to-price preparation acceptance and publication failures."""

import io
import json
import os
import socket
import zipfile
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import httpx
import pandas as pd  # type: ignore[import-untyped]
import pytest

from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.evidence import ApplicablePeriodV1
from stock_research_llm_orchestrator.preparation import research_step
from stock_research_llm_orchestrator.preparation.initial_research_input import (
    build_initial_research_input,
    validate_initial_research_input,
)
from stock_research_llm_orchestrator.preparation.market_evidence import (
    CalendarInput,
    CalendarProvenance,
    JpxSnapshotProvenance,
    MarketPreparationResult,
    prepare_market_evidence,
    validate_market_preparation,
)
from stock_research_llm_orchestrator.preparation.market_evidence_cli import main
from stock_research_llm_orchestrator.preparation.public_ir_follow_up import acquire_public_ir_follow_up
from stock_research_llm_orchestrator.preparation.research_draft import (
    DraftInput,
    DraftOutput,
    build_bundle,
    validate_bundle,
)
from stock_research_llm_orchestrator.preparation.research_follow_up import validate_follow_up
from stock_research_llm_orchestrator.preparation.storage import read_bundle
from stock_research_llm_orchestrator.preparation.task_input import create_human_selected_task
from stock_research_llm_orchestrator.sources.yfinance.adapter import YfinanceDailyAdapter
from stock_research_llm_orchestrator.sources.yfinance.native_history import HistoryAcquisitionError, NativeHistoryClient
from stock_research_llm_orchestrator.sources.yfinance.normalization import COLUMNS, TradingDates


NOW = datetime(2026, 9, 26, 9, tzinfo=UTC)
START, END = date(2023, 9, 26), date(2026, 9, 26)


def _workbook(category: str = "プライム（内国株式）", code: str = "7203") -> bytes:
    header = (
        "日付",
        "コード",
        "銘柄名",
        "市場・商品区分",
        "33業種コード",
        "33業種区分",
        "17業種コード",
        "17業種区分",
        "規模コード",
        "規模区分",
    )
    row = ("20260831", code, "Synthetic issuer", category, "", "", "", "", "", "")
    shared = (
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        + "".join(f"<si><t>{value}</t></si>" for value in (*header, *row))
        + "</sst>"
    )
    rows = []
    for number in (1, 2):
        cells = "".join(
            f'<c r="{chr(65 + col)}{number}" t="s"><v>{(number - 1) * 10 + col}</v></c>' for col in range(10)
        )
        rows.append(f'<row r="{number}">{cells}</row>')
    sheet = (
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
        + "".join(rows)
        + "</sheetData></worksheet>"
    )
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name, content in (("xl/sharedStrings.xml", shared), ("xl/worksheets/sheet1.xml", sheet)):
            archive.writestr(zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0)), content)
    return stream.getvalue()


def _provenance(body: bytes) -> JpxSnapshotProvenance:
    return JpxSnapshotProvenance(
        physical_attempt_id="synthetic-jpx-attempt", retrieved_at=NOW.isoformat(), sha256=sha256(body).hexdigest()
    )


class Scenario:
    """Real native client with synthetic source bytes and a recording ticker."""

    def __init__(self, root: Path, code: str = "7203") -> None:
        """Keep all policy and normalization logic real while replacing provider I/O."""
        fixture = json.loads((Path(__file__).parents[1] / "fixtures/market_evidence/series.json").read_bytes())
        self.frame = pd.DataFrame(
            {column: fixture[column] for column in COLUMNS},
            index=pd.date_range(START, END - timedelta(days=1), freq="B", tz="Asia/Tokyo"),
        )
        self.calls = 0
        self.failure = False
        self.currency = "JPY"
        self.response_symbol = f"{code}.T"
        self.code = code
        self.client = NativeHistoryClient(root / ".rate", ticker_factory=lambda _: self, clock=lambda: NOW.timestamp())
        self.adapter = YfinanceDailyAdapter(history_client=self.client)
        self.root = root

    def history(self, **kwargs: object) -> object:
        """Return a synthetic three-year table and already-cached chart metadata."""
        self.calls += 1
        assert kwargs["auto_adjust"] is False
        if self.failure:
            raise RuntimeError("do-not-leak")
        self._price_history = SimpleNamespace(
            _history_metadata={
                "currency": self.currency,
                "symbol": self.response_symbol,
                "exchangeTimezoneName": "Asia/Tokyo",
                "instrumentType": "EQUITY",
            }
        )
        return self.frame

    def calendar(self) -> CalendarInput:
        """Create source-bound synthetic trading dates, not a real exchange calendar."""
        calendar = TradingDates(
            period=ApplicablePeriodV1(start_date=START.isoformat(), end_date="2026-09-25"),
            dates=tuple(stamp.date().isoformat() for stamp in self.frame.index),
        )
        body = calendar.model_dump_json().encode()
        return CalendarInput(
            body=body,
            provenance=CalendarProvenance(
                source_reference="fixture://synthetic-weekday-calendar",
                retrieved_at=NOW.isoformat(),
                sha256=sha256(body).hexdigest(),
            ),
        )

    def prepare(
        self, *, body: bytes | None = None, code: str | None = None, calendar: CalendarInput | None = None
    ) -> MarketPreparationResult:
        """Exercise the public internal preparation boundary."""
        code = self.code if code is None else code
        body = _workbook(code=self.code) if body is None else body
        task = create_human_selected_task(
            code, 1, mic="XTKS", task_id_factory=lambda: "task-native-1", accepted_at_factory=lambda: NOW
        )
        return prepare_market_evidence(
            task=task,
            jpx_body=body,
            jpx_provenance=_provenance(body),
            start=START,
            end=END,
            adapter=self.adapter,
            root=self.root,
            destination_name="prepared",
            prepared_at=NOW,
            calendar=calendar,
        )


@pytest.fixture(autouse=True)
def _deny_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def reject(*args: object, **kwargs: object) -> None:
        pytest.fail("offline acceptance attempted network I/O")

    monkeypatch.setattr(socket.socket, "connect", reject)
    monkeypatch.setattr(socket, "getaddrinfo", reject)


def test_native_e2e_resolves_every_artifact_without_claiming_analysis_completion(tmp_path: Path) -> None:
    """Publish reproducible native evidence with actual provenance and explicit remaining freshness gaps."""
    scenario = Scenario(tmp_path)
    scenario.prepare(calendar=scenario.calendar())
    files = {path.name: path.read_bytes() for path in (tmp_path / "prepared").iterdir()}
    validate_market_preparation(files)
    index = json.loads(files["index.json"])
    assert scenario.calls == 1
    assert index["price_quality_passed"] is True
    assert index["analysis_ready"] is False
    assert index["status"] == "prepared_with_gaps"
    assert index["jpx_verification"]["status"] == "verified_eligible"
    assert {issue["code"] for issue in index["issues"]} == {"jpx_snapshot_freshness_unconfirmed"}
    assert json.loads(files["history-metadata.json"])["retrieved_at"] == NOW.isoformat()
    assert b"http_body_retained" in files["history-metadata.json"]
    other_root = tmp_path / "other"
    other_root.mkdir()
    other = Scenario(other_root)
    other.prepare(calendar=other.calendar())
    assert all((other_root / "prepared" / name).read_bytes() == value for name, value in files.items())


def test_initial_research_input_retains_true_source_references_and_exact_market_bytes(tmp_path: Path) -> None:
    """The draft cites JPX bytes and a library call without inventing a price HTTP URL."""
    scenario = Scenario(tmp_path)
    scenario.prepare(calendar=scenario.calendar())
    market = {path.name: path.read_bytes() for path in (tmp_path / "prepared").iterdir()}
    files = build_initial_research_input(market)
    validate_initial_research_input(files)
    data = DraftInput.model_validate_json(files["input.json"])
    assert data.security_code == "7203"
    assert (
        data.evidence[0].source_url
        == "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xlsx"
    )
    assert data.evidence[0].source_sha256 == sha256(market["jpx.xlsx"]).hexdigest()
    assert data.evidence[1].source_url is None
    assert data.evidence[1].source_reference == "yfinance:Ticker.history:7203.T"
    assert data.evidence[1].source_sha256 == sha256(market["prices.csv"]).hexdigest()
    assert "Yahoo Finance" in data.evidence[1].text
    assert "http_body_retained" in data.evidence[1].text
    assert json.loads(files["manifest.json"])["analysis_ready"] is False
    changed = dict(files)
    changed["input.json"] += b" "
    with pytest.raises(ValueError, match="initial_research_input_binding_invalid"):
        validate_initial_research_input(changed)
    changed = dict(files)
    changed["market/prices.csv"] += b" "
    with pytest.raises(ValueError):
        validate_initial_research_input(changed)


def test_second_security_reaches_reanalysis_with_replayable_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A different code flows through market, draft, public IR, and another draft."""
    scenario = Scenario(tmp_path, code="6758")
    scenario.prepare(calendar=scenario.calendar())
    initial = build_initial_research_input(read_bundle(tmp_path / "prepared"))
    validate_initial_research_input(initial)
    data = DraftInput.model_validate_json(initial["input.json"])
    assert data.security_code == "6758"
    assert data.evidence[1].source_reference == "yfinance:Ticker.history:6758.T"

    first = DraftOutput.model_validate_json(
        json.dumps(
            {
                "claims": [],
                "missing_information": ["Segment results are unavailable."],
                "follow_up_requests": [
                    {
                        "question": "What were the segment results?",
                        "reason": "The current market slice has no financial filing.",
                        "suggested_source_url": "https://example.com/ir/segments",
                    }
                ],
            }
        )
    )
    prior = tmp_path / "prior"
    prior.mkdir()
    for name, body in build_bundle(data, first).items():
        (prior / name).write_bytes(body)
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", 443))],
    )

    def respond(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://example.com/ir/segments"
        return httpx.Response(200, content=b"Segment revenue was reported.", headers={"Content-Type": "text/plain"})

    def fetch(
        *,
        prior: Path,
        url: str,
        allowed_host: str,
        request_index: int,
        runtime: Path,
        runs: Path,
        destination: str,
        task_id: str,
    ) -> Path:
        return acquire_public_ir_follow_up(
            prior=prior,
            url=url,
            allowed_host=allowed_host,
            request_index=request_index,
            runtime=runtime,
            runs=runs,
            destination=destination,
            task_id=task_id,
            clock=lambda: NOW,
            transport=httpx.MockTransport(respond),
        )

    def analyze(next_input: DraftInput, _approval_root: Path) -> DraftOutput:
        assert next_input.security_code == "6758"
        assert next_input.evidence[-1].text == "Segment revenue was reported."
        return DraftOutput(
            claims=(), missing_information=("Profit by segment remains unknown.",), follow_up_requests=()
        )

    monkeypatch.setattr(research_step, "acquire_public_ir_follow_up", fetch)
    monkeypatch.setattr(research_step, "run_codex", analyze)
    follow_up, draft = research_step.advance_research_step(
        prior=prior,
        allowed_host="example.com",
        runtime=tmp_path / "runtime",
        runs=tmp_path / "runs",
        follow_up_destination="follow-up-6758",
        draft_destination="reanalysis-6758",
        task_id="research-6758",
    )
    follow_up_files = read_bundle(follow_up)
    validate_follow_up(follow_up_files)
    validate_bundle(read_bundle(draft))
    assert DraftOutput.model_validate_json((draft / "draft.json").read_bytes()).missing_information == (
        "Profit by segment remains unknown.",
    )
    raw = next((tmp_path / "runs/research-6758").rglob("body.bin"))
    assert sha256(raw.read_bytes()).hexdigest() == json.loads(follow_up_files["manifest.json"])["source_sha256"]


@pytest.mark.parametrize(
    "category,code", [("ETF・ETN", "7203"), ("プライム（外国株式）", "7203"), ("プライム（内国株式）", "9999")]
)
def test_ineligible_identity_prevents_native_acquisition(tmp_path: Path, category: str, code: str) -> None:
    """Membership and class checks run before any native call."""
    scenario = Scenario(tmp_path)
    with pytest.raises(ValueError, match="not_eligible"):
        scenario.prepare(body=_workbook(category), code=code)
    assert scenario.calls == 0
    assert not (tmp_path / "prepared").exists()


def test_tampered_jpx_bytes_are_rejected_before_acquisition(tmp_path: Path) -> None:
    """A matching code in unverified bytes is insufficient provenance."""
    scenario = Scenario(tmp_path)
    body = _workbook()
    task = create_human_selected_task(
        "7203", 1, mic="XTKS", task_id_factory=lambda: "task-1", accepted_at_factory=lambda: NOW
    )
    with pytest.raises(ValueError, match="hash_mismatch"):
        prepare_market_evidence(
            task=task,
            jpx_body=body + b"tampered",
            jpx_provenance=_provenance(body),
            start=START,
            end=END,
            adapter=scenario.adapter,
            root=tmp_path,
            destination_name="prepared",
            prepared_at=NOW,
        )
    assert scenario.calls == 0


def test_missing_calendar_and_conflicting_identity_never_pass_acceptance(tmp_path: Path) -> None:
    """Store a reviewable slice while preserving unresolved or conflicting source facts."""
    scenario = Scenario(tmp_path)
    scenario.response_symbol = "1301.T"
    scenario.prepare()
    index = json.loads((tmp_path / "prepared/index.json").read_bytes())
    assert not index["price_quality_passed"]
    assert not index["provider_identity_verified"]
    assert {issue["code"] for issue in index["issues"]} >= {"trading_dates_unconfirmed", "provider_identity_conflict"}


def test_provider_failure_is_not_ineligibility_and_publishes_nothing(tmp_path: Path) -> None:
    """Preserve technical failure semantics without source exception text."""
    scenario = Scenario(tmp_path)
    scenario.failure = True
    with pytest.raises(HistoryAcquisitionError, match="yfinance_history_failed"):
        scenario.prepare()
    assert scenario.calls == 1
    assert not (tmp_path / "prepared").exists()


def test_changed_bytes_and_missing_reference_fail_persisted_validation(tmp_path: Path) -> None:
    """Hashes bind the stored data, not merely self-consistent model identifiers."""
    scenario = Scenario(tmp_path)
    scenario.prepare()
    files = {path.name: path.read_bytes() for path in (tmp_path / "prepared").iterdir()}
    with pytest.raises(ValueError, match="hash_mismatch"):
        validate_market_preparation({**files, "prices.csv": files["prices.csv"] + b"changed"})
    del files["jpx.xlsx"]
    with pytest.raises(ValueError, match="reference_set"):
        validate_market_preparation(files)


def test_publication_failure_leaves_no_completed_bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed directory rename never exposes partial evidence."""
    original = os.rename

    def fail_publish(source: str | Path, destination: str | Path) -> None:
        if Path(destination).name == "prepared":
            raise OSError("synthetic disk failure")
        original(source, destination)

    monkeypatch.setattr(os, "rename", fail_publish)
    with pytest.raises(OSError, match="disk failure"):
        Scenario(tmp_path).prepare()
    assert not (tmp_path / "prepared").exists()
    assert not list(tmp_path.glob(".staging-*"))


def test_existing_destination_prevents_download(tmp_path: Path) -> None:
    """Reject collisions before consuming provider quota or changing existing bytes."""
    (tmp_path / "prepared").mkdir()
    (tmp_path / "prepared/keep").write_bytes(b"old")
    scenario = Scenario(tmp_path)
    with pytest.raises(FileExistsError):
        scenario.prepare()
    assert scenario.calls == 0
    assert (tmp_path / "prepared/keep").read_bytes() == b"old"


def test_cli_requires_explicit_network_opt_in() -> None:
    """A missing opt-in fails before file loading or provider invocation."""
    with pytest.raises(SystemExit) as result:
        main(["--code", "7203"])
    assert result.value.code == 2


def test_cli_include_today_uses_exclusive_tomorrow_without_provider_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pass the completed current day as the exclusive next-day boundary."""
    from stock_research_llm_orchestrator.preparation import market_evidence_cli

    class FixedClock(datetime):
        @classmethod
        def now(cls, tz: object = None) -> FixedClock:
            return cls(2026, 10, 1, 14, 0, tzinfo=UTC)

    seen: list[date] = []

    def reject_after_boundary(**kwargs: object) -> None:
        end = kwargs["end"]
        assert isinstance(end, date)
        seen.append(end)
        raise ValueError("test_boundary")

    (tmp_path / "jpx.xlsx").write_bytes(_workbook())
    (tmp_path / "jpx.json").write_text(_provenance(_workbook()).model_dump_json())
    monkeypatch.setattr(market_evidence_cli, "datetime", FixedClock)
    monkeypatch.setattr(market_evidence_cli, "prepare_market_evidence", reject_after_boundary)
    assert (
        main(
            [
                "--allow-network",
                "--include-today",
                "--code",
                "7203",
                "--evaluation-policy-version",
                "1",
                "--jpx-body",
                str(tmp_path / "jpx.xlsx"),
                "--jpx-metadata",
                str(tmp_path / "jpx.json"),
                "--output",
                str(tmp_path / "prepared"),
            ]
        )
        == 1
    )
    assert seen == [date(2026, 10, 2)]


def test_cli_preserves_partial_acceptance_exit_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Exercise argument parsing, native preparation and a reviewable non-success summary."""
    from stock_research_llm_orchestrator.preparation import market_evidence_cli

    class FixedClock(datetime):
        @classmethod
        def now(cls, tz: object = None) -> FixedClock:
            return cls.fromtimestamp(NOW.timestamp(), UTC)

    scenario = Scenario(tmp_path)
    body = _workbook()
    (tmp_path / "jpx.xlsx").write_bytes(body)
    (tmp_path / "jpx.json").write_text(_provenance(body).model_dump_json())
    monkeypatch.setattr(market_evidence_cli, "datetime", FixedClock)
    monkeypatch.setattr(market_evidence_cli, "YfinanceDailyAdapter", lambda: scenario.adapter)
    arguments = [
        "--allow-network",
        "--code",
        "7203",
        "--evaluation-policy-version",
        "1",
        "--jpx-body",
        str(tmp_path / "jpx.xlsx"),
        "--jpx-metadata",
        str(tmp_path / "jpx.json"),
        "--output",
        str(tmp_path / "prepared"),
    ]
    assert main(arguments) == 2
    summary = json.loads(capsys.readouterr().out)
    assert summary["status"] == "prepared_with_gaps"
    assert summary["analysis_ready"] is False
    assert scenario.calls == 1
    assert main(arguments) == 1
    assert scenario.calls == 1


def test_corrupt_calendar_prevents_provider_call(tmp_path: Path) -> None:
    """Validate supplemental provenance before spending provider quota."""
    scenario = Scenario(tmp_path)
    original = scenario.calendar()
    bad = CalendarInput(body=original.body + b" ", provenance=original.provenance)
    with pytest.raises(ValueError, match="calendar_hash_mismatch"):
        scenario.prepare(calendar=bad)
    assert scenario.calls == 0


@pytest.mark.parametrize(
    ("now", "end", "allowed"),
    [
        (datetime(2026, 9, 29, 6, 29, 59, tzinfo=UTC), date(2026, 9, 30), False),
        (datetime(2026, 9, 29, 6, 30, tzinfo=UTC), date(2026, 9, 30), True),
        (datetime(2026, 9, 29, 14, tzinfo=UTC), date(2026, 9, 30), True),
        (datetime(2026, 9, 29, 14, tzinfo=UTC), date(2026, 10, 1), False),
        (datetime(2026, 9, 26, 9, tzinfo=UTC), date(2026, 9, 27), False),
        (datetime(2026, 12, 27, 9, tzinfo=UTC), date(2026, 12, 28), False),
    ],
)
def test_same_day_acquisition_requires_approved_session_completion(
    tmp_path: Path, now: datetime, end: date, allowed: bool
) -> None:
    """Allow exclusive tomorrow only after the approved current session closes."""
    scenario = Scenario(tmp_path)
    scenario.client = NativeHistoryClient(
        tmp_path / ".rate-current", ticker_factory=lambda _: scenario, clock=lambda: now.timestamp()
    )
    scenario.adapter = YfinanceDailyAdapter(history_client=scenario.client)
    for day in ("2026-09-28", "2026-09-29"):
        scenario.frame.loc[pd.Timestamp(day, tz="Asia/Tokyo")] = scenario.frame.iloc[-1]
    body = _workbook()
    task = create_human_selected_task(
        "7203", 1, mic="XTKS", task_id_factory=lambda: "task-current", accepted_at_factory=lambda: NOW
    )

    def prepare() -> MarketPreparationResult:
        return prepare_market_evidence(
            task=task,
            jpx_body=body,
            jpx_provenance=_provenance(body),
            start=START,
            end=end,
            adapter=scenario.adapter,
            root=tmp_path,
            destination_name="current",
            prepared_at=now,
        )

    if allowed:
        prepare()
        files = {path.name: path.read_bytes() for path in (tmp_path / "current").iterdir()}
        validate_market_preparation(files)
        assert json.loads(files["history-metadata.json"])["arguments"]["end"] == end.isoformat()
        assert json.loads(files["normalized.json"])["requested_period"]["end_date"] == "2026-09-29"
        assert scenario.calls == 1
    else:
        with pytest.raises(ValueError, match="unfinished_day|outside_profile_period"):
            prepare()
        assert scenario.calls == 0
