"""Daily acquisition, immutable replay and stop-on-failure coverage."""

import json
import subprocess
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from stock_research_llm_orchestrator.preparation.dukascopy_fx import (
    FxMetadataV2,
    _index,
    acquire_dukascopy_fx,
    normalized,
    validate_dukascopy_fx,
)
from stock_research_llm_orchestrator.preparation.storage import read_bundle
from stock_research_llm_orchestrator.sources.dukascopy.daily import parse_daily, urls, year_url


ROOT = Path(__file__).parents[2]
NOW = datetime(2026, 9, 26, 12, tzinfo=UTC)


def body(year: int) -> bytes:
    """Build synthetic delta-encoded daily data with an intentional missing day."""
    return json.dumps(
        dict(
            timestamp=int(datetime(year, 9, 24, tzinfo=UTC).timestamp()) * 1000,
            shift=86400000,
            multiplier=0.001,
            open=150,
            high=151,
            low=149,
            close=150,
            times=[0, 2],
            opens=[0, 1],
            highs=[0, 1],
            lows=[0, 1],
            closes=[1, 2],
            volumes=[1, 2],
        )
    ).encode()


def test_node_contract() -> None:
    """Run all Node builtin tests without the IPC isolation affected by network disabling."""
    result = subprocess.run(["node", str(ROOT / "node/dukascopy/bridge.test.cjs")], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "# tests 4" in result.stdout


def test_daily_no_fill_and_confirmation() -> None:
    """Exclude synthetic carry-forward days and distinguish unfinished candles."""
    series = parse_daily(body(2026), year_url(2026, NOW), 2026, NOW)
    assert [r.on for r in series.rows] == ["2026-09-24", "2026-09-26"]
    assert [r.confirmed for r in series.rows] == [True, False]
    assert str(series.rows[0].close) == "150.001"


def test_urls_are_annual() -> None:
    """Daily period length does not multiply physical requests."""
    assert len(urls(date(2023, 9, 26), date(2026, 9, 25), NOW)) == 4
    assert len(urls(date(2026, 9, 24), date(2026, 9, 25), NOW)) == 1
    with pytest.raises(ValueError):
        urls(date(2026, 9, 24), date(2026, 9, 26), NOW)


def acquire(tmp_path: Path, handler: Callable[[httpx.Request], httpx.Response]) -> Path:
    """Exercise the real coordinator with a deterministic ticking clock."""
    current = [NOW]

    def clock() -> datetime:
        return current[0]

    def sleep(seconds: float) -> None:
        current[0] += timedelta(seconds=seconds)

    acquire_dukascopy_fx(
        config=ROOT / "config",
        runtime=tmp_path / "runtime",
        runs=tmp_path / "runs",
        destination="accepted",
        task_id="test-fx",
        start=date(2025, 9, 24),
        end=date(2026, 9, 25),
        clock=clock,
        sleep=sleep,
        transport=httpx.MockTransport(handler),
    )
    return tmp_path / "runs/accepted"


def test_acquire_annual_and_replay(tmp_path: Path) -> None:
    """Two annual sends each publish raw before one logical success."""
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        year = 2026 if request.url.query else int(request.url.path.rsplit("/", 1)[1])
        seen.append(year)
        return httpx.Response(200, content=body(year), headers={"Content-Type": "application/json"})

    saved = read_bundle(acquire(tmp_path, handler))
    assert seen == [2026, 2025]
    validate_dukascopy_fx(saved)
    assert "year_requested_at" not in json.loads(saved["metadata.json"])
    assert (
        FxMetadataV2.model_validate_json(saved["metadata.json"]).model_dump_json(indent=2, exclude_none=True).encode()
        == saved["metadata.json"]
    )
    saved["raw/2025.bin"] += b" "
    with pytest.raises(ValueError, match="raw_reference"):
        validate_dukascopy_fx(saved)


@pytest.fixture(scope="module")
def composite_fx_files(tmp_path_factory: pytest.TempPathFactory) -> dict[str, bytes]:
    """Combine original receipts from two real offline-coordinated acquisitions."""
    root = tmp_path_factory.mktemp("composite-fx")

    def handler(request: httpx.Request) -> httpx.Response:
        year = 2026 if request.url.query else int(request.url.path.rsplit("/", 1)[1])
        return httpx.Response(200, content=body(year), headers={"Content-Type": "application/json"})

    old = read_bundle(acquire(root / "old", handler))
    later = NOW + timedelta(days=3)
    acquire_dukascopy_fx(
        config=ROOT / "config",
        runtime=root / "later-runtime",
        runs=root / "later-runs",
        destination="accepted",
        task_id="composite-fx",
        start=date(2026, 1, 1),
        end=date(2026, 9, 28),
        clock=lambda: later,
        transport=httpx.MockTransport(handler),
    )
    new = read_bundle(root / "later-runs/accepted")
    old_meta = json.loads(old["metadata.json"])
    new_meta = json.loads(new["metadata.json"])
    receipts = [r for r in old_meta["receipts"] if r["year"] == 2025] + new_meta["receipts"]
    metadata = {
        **old_meta,
        "period_end": "2026-09-28",
        "receipts": receipts,
        "year_requested_at": {"2025": old_meta["requested_at"], "2026": new_meta["requested_at"]},
    }
    files = {**old, "raw/2026.bin": new["raw/2026.bin"], "metadata.json": json.dumps(metadata).encode()}
    files["normalized.json"] = (
        normalized(files, FxMetadataV2.model_validate_json(files["metadata.json"])).model_dump_json(indent=2).encode()
    )
    files["index.json"] = _index(files).model_dump_json(indent=2).encode()
    assert read_bundle(root / "old/runs/accepted") == old
    assert read_bundle(root / "later-runs/accepted") == new
    return files


def test_composite_replays_original_receipts(composite_fx_files: dict[str, bytes]) -> None:
    """An expanded endpoint uses its own request time without changing old receipts."""
    validate_dukascopy_fx(composite_fx_files)
    metadata = FxMetadataV2.model_validate_json(composite_fx_files["metadata.json"])
    assert metadata.period_end == "2026-09-28"
    assert metadata.receipts[0].received_at < metadata.receipts[1].received_at
    assert [r.confirmed for r in normalized(composite_fx_files, metadata).rows if r.on == "2026-09-26"] == [True]
    equivalent = metadata.model_copy(update={"requested_at": NOW.astimezone(timezone(timedelta(hours=3))).isoformat()})
    assert normalized(composite_fx_files, equivalent) == normalized(composite_fx_files, metadata)


@pytest.mark.parametrize(
    "change",
    ["absent", "missing", "extra", "noncanonical", "earliest", "naive", "after_receipt", "unfinished", "reversed"],
)
def test_composite_rejects_invalid_request_times(composite_fx_files: dict[str, bytes], change: str) -> None:
    """Explicit components preserve inventory, offset, receipt and completed-day boundaries."""
    metadata = json.loads(composite_fx_files["metadata.json"])
    times = metadata["year_requested_at"]
    if change == "absent":
        del metadata["year_requested_at"]
    elif change == "missing":
        del times["2025"]
    elif change == "extra":
        times["2024"] = NOW.isoformat()
    elif change == "noncanonical":
        times["02025"] = times.pop("2025")
    elif change == "earliest":
        metadata["requested_at"] = (NOW + timedelta(seconds=1)).isoformat()
    elif change == "naive":
        times["2026"] = "2026-09-29T12:00:00"
    elif change == "after_receipt":
        times["2026"] = (NOW + timedelta(days=3, seconds=1)).isoformat()
    elif change == "unfinished":
        times["2026"] = NOW.isoformat()
    elif change == "reversed":
        metadata["period_start"] = "2026-09-29"
    with pytest.raises(ValueError):
        normalized(composite_fx_files, FxMetadataV2.model_validate_json(json.dumps(metadata)))


@pytest.mark.parametrize("status", [429, 500, 302])
def test_http_failure_stops_after_first_send(tmp_path: Path, status: int) -> None:
    """No retry or second year after a provider failure."""
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, content=b"{}", headers={"Content-Type": "application/json", "Retry-After": "60"})

    with pytest.raises(ValueError):
        acquire(tmp_path, handler)
    assert len(seen) == 1
    assert not (tmp_path / "runs/accepted").exists()


def test_source_failure_keeps_diagnostic(tmp_path: Path) -> None:
    """Malformed received data is saved unaccepted and prevents later sends."""
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"{}", headers={"Content-Type": "application/json"})

    with pytest.raises(ValueError):
        acquire(tmp_path, handler)
    assert len(seen) == 1
    assert (tmp_path / "runs/accepted/unaccepted-body.bin").read_bytes() == b"{}"
    assert not (tmp_path / "runs/accepted/index.json").exists()


def test_v2_join_uses_utc_bid_and_replays(tmp_path: Path) -> None:
    """Preserve source decimals and classify absent FX without filling it."""
    from decimal import Decimal, localcontext

    from stock_research_llm_orchestrator.preparation.price_fx_v2 import join_price_fx_v2, validate_price_fx

    from .test_price_acceptance import AcceptanceScenario

    scenario = AcceptanceScenario(tmp_path)
    scenario.accept()
    current = [NOW]

    def sleep(seconds: float) -> None:
        current[0] += timedelta(seconds=seconds)

    def handler(request: httpx.Request) -> httpx.Response:
        year = 2026 if request.url.query else int(request.url.path.rsplit("/", 1)[1])
        return httpx.Response(200, content=body(year), headers={"Content-Type": "application/json"})

    acquire_dukascopy_fx(
        config=ROOT / "config",
        runtime=tmp_path / "runtime",
        runs=tmp_path / "fx",
        destination="accepted",
        task_id="join-fx",
        start=date(2023, 9, 26),
        end=date(2026, 9, 25),
        clock=lambda: current[0],
        sleep=sleep,
        transport=httpx.MockTransport(handler),
    )
    with localcontext() as context:
        context.prec = 3
        result = join_price_fx_v2(tmp_path / "accepted", tmp_path / "fx/accepted", tmp_path / "joined")
    assert result.version == 2
    assert result.status == "incomplete"
    assert result.rows[0].usd_jpy == Decimal("150.003")
    assert result.rows[0].close_usd == Decimal("0.7033192669479943734458644160")
    assert result.rows[1].missing_reasons == ("fx_date_missing",)
    validate_price_fx(read_bundle(tmp_path / "joined"))


def test_raw_publication_failure_does_not_finalize_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Publication failures retain the active fence for recovery, never success."""
    import sqlite3

    from stock_research_llm_orchestrator.requests.raw_artifacts import RawArtifactPublisher

    def fail(*args: object, **kwargs: object) -> None:
        raise OSError("simulated publication failure")

    monkeypatch.setattr(RawArtifactPublisher, "publish", fail)
    with pytest.raises(OSError):
        acquire(
            tmp_path, lambda _: httpx.Response(200, content=body(2026), headers={"Content-Type": "application/json"})
        )
    database = next((tmp_path / "runtime").glob("*.sqlite3"))
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM logical_results").fetchone()[0] == 0


@pytest.mark.parametrize(
    "change",
    [
        {"times": [0, 0]},
        {"times": [0, -1]},
        {"shift": 60000},
        {"closes": [None, 1]},
        {"closes": [1]},
        {"close": 0, "closes": [0, 0]},
        {"timestamp": 0},
    ],
)
def test_reject_invalid_raw(change: dict[str, object]) -> None:
    """Malformed source rows never become accepted exchange rates."""
    raw = json.loads(body(2026))
    raw.update(change)
    with pytest.raises(ValueError):
        parse_daily(json.dumps(raw).encode(), year_url(2026, NOW), 2026, NOW)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/v1/candles/day/USD-JPY/BID/2026",
        "https://jetta.dukascopy.com/v1/candles/day/USD-JPY/ASK/2026",
        "https://jetta.dukascopy.com/v1/candles/minute/USD-JPY/BID/2026",
        "https://jetta.dukascopy.com/v1/candles/day/USD-JPY/BID?from=1",
        "https://jetta.dukascopy.com/v1/candles/day/USD-JPY/BID/2025",
    ],
)
def test_unapproved_target_rejected_before_send(url: str) -> None:
    """Reject origin, side, timeframe and year substitutions before transport."""
    from stock_research_llm_orchestrator.sources.dukascopy.daily import intent_for_url
    from stock_research_llm_orchestrator.sources.dukascopy.httpx_transport import (
        DukascopyHttpClientPolicy,
        DukascopyHttpTransportError,
        DukascopyPhysicalTransport,
    )
    from stock_research_llm_orchestrator.sources.protocol import SourceParameter

    valid = intent_for_url(year_url(2026, NOW), 2026, NOW)
    invalid = valid.model_copy(update={"parameters": (SourceParameter(name="url", value=url),)})

    def reject(request: httpx.Request) -> httpx.Response:
        pytest.fail("unapproved target reached transport")

    physical = DukascopyPhysicalTransport(
        invalid,
        DukascopyHttpClientPolicy(
            connect_timeout_seconds=10, read_timeout_seconds=30, write_timeout_seconds=10, pool_timeout_seconds=10
        ),
        httpx.MockTransport(reject),
    )
    with pytest.raises(DukascopyHttpTransportError):
        physical(invalid.to_transport_request("logical", "physical"))


def test_later_year_failure_keeps_partial_raw(tmp_path: Path) -> None:
    """A second-year failure leaves the first committed raw but no accepted bundle."""
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200 if len(seen) == 1 else 500, content=body(2026), headers={"Content-Type": "application/json"}
        )

    with pytest.raises(ValueError):
        acquire(tmp_path, handler)
    assert len(seen) == 2
    assert not (tmp_path / "runs/accepted").exists()
    assert len(list((tmp_path / "runs/test-fx").rglob("body.bin"))) == 1


def test_failed_logical_keeps_replayable_successful_year_receipt(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Keep actual receipt time after the next year's HTTP failure without relabeling success."""
    from stock_research_llm_orchestrator.preparation.dukascopy_fx import validate_dukascopy_year_receipt
    from stock_research_llm_orchestrator.preparation.fx_cli import main
    from stock_research_llm_orchestrator.requests.storage import DATABASE_FILENAME, ProductionRequestRepository

    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200 if len(seen) == 1 else 500, content=body(2026), headers={"Content-Type": "application/json"}
        )

    with pytest.raises(ValueError):
        acquire(tmp_path, handler)
    saved_path = next((tmp_path / "runs").glob("year-receipts-*/2026"))
    artifact = validate_dukascopy_year_receipt(
        read_bundle(saved_path), runtime=tmp_path / "runtime", runs=tmp_path / "runs"
    )
    assert artifact.receipt.received_at == NOW.isoformat()
    assert artifact.requested_at == NOW.isoformat()
    assert len(seen) == 2 and not (tmp_path / "runs/accepted").exists()
    repo = ProductionRequestRepository(tmp_path / "runtime" / DATABASE_FILENAME)
    result = repo.logical_result(artifact.receipt.raw_reference.logical_request_id)
    assert result is not None and result.outcome == "failed" and result.error_code == "provider_error"
    assert (
        main(
            [
                "validate-year-receipt",
                "--input",
                str(saved_path),
                "--runtime",
                str(tmp_path / "runtime"),
                "--runs",
                str(tmp_path / "runs"),
            ]
        )
        == 0
    )
    assert "offline_replay" in capsys.readouterr().out
    assert repo.logical_result(artifact.receipt.raw_reference.logical_request_id) == result


@pytest.mark.parametrize(
    "change", ["index", "body", "time", "task", "physical", "publication", "request", "response", "receipt", "runtime"]
)
def test_year_receipt_rejects_changed_provenance(tmp_path: Path, change: str) -> None:
    """Require original runtime identities and exact saved acquisition artifacts."""
    import sqlite3
    from hashlib import sha256

    from stock_research_llm_orchestrator.preparation.dukascopy_fx import validate_dukascopy_year_receipt
    from stock_research_llm_orchestrator.requests.storage import DATABASE_FILENAME

    acquire(
        tmp_path,
        lambda request: httpx.Response(
            200, content=body(2026 if request.url.query else 2025), headers={"Content-Type": "application/json"}
        ),
    )
    saved_path = next((tmp_path / "runs").glob("year-receipts-*/2026"))
    files = read_bundle(saved_path)
    artifact = json.loads(files["year-receipt.json"])
    reference = artifact["receipt"]["raw_reference"]
    raw = tmp_path / "runs" / reference["task_id"] / reference["relative_path"]
    runtime = tmp_path / "runtime"
    if change == "runtime":
        runtime = tmp_path / "other-runtime"
        runtime.mkdir(mode=0o700)
    elif change == "index":
        files["index.json"] = b"{}"
    elif change == "body":
        (raw / "body.bin").write_bytes(body(2026) + b" ")
    elif change == "time":
        artifact["receipt"]["received_at"] = (NOW + timedelta(seconds=1)).isoformat()
    elif change == "task":
        artifact["receipt"]["raw_reference"]["task_id"] = "other-task"
    elif change in {"physical", "publication"}:
        table, field, identifier = (
            ("physical_attempts", "physical_attempt_id", reference["physical_attempt_id"])
            if change == "physical"
            else ("raw_publications", "publication_id", reference["publication_id"])
        )
        with sqlite3.connect(tmp_path / "runtime" / DATABASE_FILENAME) as connection:
            connection.execute(f"UPDATE {table} SET state='failed' WHERE {field}=?", (identifier,))
    else:
        target = raw / (change + ".json")
        target.write_text("{}")
    if change in {"time", "task"}:
        files["year-receipt.json"] = json.dumps(artifact).encode()
        files["index.json"] = json.dumps(
            {name: sha256(content).hexdigest() for name, content in files.items() if name != "index.json"}
        ).encode()
    with pytest.raises(ValueError, match="fx_year_receipt"):
        validate_dukascopy_year_receipt(files, runtime=runtime, runs=tmp_path / "runs")
    if change == "runtime":
        assert not (runtime / DATABASE_FILENAME).exists()


def test_year_receipt_publication_failure_stops_next_send(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A committed raw without a durable year receipt cannot trigger later acquisition."""
    from stock_research_llm_orchestrator.preparation import dukascopy_fx as fx
    from stock_research_llm_orchestrator.preparation.storage import PreparationReceipt, PreparationValidator
    from stock_research_llm_orchestrator.requests.storage import DATABASE_FILENAME, ProductionRequestRepository

    original = fx.publish_preparation
    seen = []

    def publish(
        root: Path, destination_name: str, files: Mapping[str, bytes], *, validator: PreparationValidator
    ) -> PreparationReceipt:
        if root.name.startswith("year-receipts-"):
            raise OSError("simulated receipt publication failure")
        return original(root, destination_name, files, validator=validator)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=body(2026), headers={"Content-Type": "application/json"})

    monkeypatch.setattr(fx, "publish_preparation", publish)
    with pytest.raises(OSError):
        acquire(tmp_path, handler)
    assert len(seen) == 1 and not (tmp_path / "runs/accepted").exists()
    repo = ProductionRequestRepository(tmp_path / "runtime" / DATABASE_FILENAME)
    records = repo.committed_raw_records()
    assert len(records) == 1
    result = repo.logical_result(records[0].intent.logical_request_id)
    assert result is not None and result.outcome == "failed"
    assert result.error_code == "dukascopy_year_receipt_publication_failed"
