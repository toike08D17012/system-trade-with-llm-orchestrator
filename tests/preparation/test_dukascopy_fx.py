"""Daily acquisition, immutable replay and stop-on-failure coverage."""

import json
import subprocess
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from stock_research_llm_orchestrator.preparation.dukascopy_fx import acquire_dukascopy_fx, validate_dukascopy_fx
from stock_research_llm_orchestrator.preparation.fx_evidence import read_bundle, validate_fx_evidence
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
    validate_fx_evidence(saved)
    validate_dukascopy_fx(saved)
    saved["raw/2025.bin"] += b" "
    with pytest.raises(ValueError, match="raw_reference"):
        validate_dukascopy_fx(saved)


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

    from stock_research_llm_orchestrator.preparation.price_fx import join_price_fx, validate_price_fx

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
        result = join_price_fx(tmp_path / "accepted", tmp_path / "fx/accepted", tmp_path / "joined")
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
