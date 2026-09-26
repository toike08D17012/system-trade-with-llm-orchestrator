"""Validate native acquisition and durable rate protection without network I/O."""

import json
import multiprocessing
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd  # type: ignore[import-untyped]
import pytest
from yfinance.config import YfConfig  # type: ignore[import-untyped]
from yfinance.exceptions import YFRateLimitError  # type: ignore[import-untyped]

from stock_research_llm_orchestrator.sources.yfinance import YfinanceDailyAdapter, YfinanceDailyIntent
from stock_research_llm_orchestrator.sources.yfinance.native_history import (
    HistoryAcquisitionError,
    HistoryThrottledError,
    NativeHistoryClient,
)


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        {name: [1.0] for name in ("Open", "High", "Low", "Close", "Adj Close", "Volume", "Dividends", "Stock Splits")},
        index=pd.date_range("2026-09-25", periods=1, tz="Asia/Tokyo"),
    )


class _Ticker:
    def __init__(self, calls: list[dict[str, object]], failure: Exception | None = None) -> None:
        self.calls = calls
        self.failure = failure

    def history(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        assert YfConfig.network.retries == 0
        assert YfConfig.debug.hide_exceptions is False
        if self.failure:
            raise self.failure
        return _frame()


def test_native_adapter_uses_history_without_session_and_preserves_metadata(tmp_path: Path) -> None:
    """The default public adapter uses verified dates and native library sessions."""
    calls: list[dict[str, object]] = []
    symbols: list[str] = []

    def ticker(symbol: str) -> _Ticker:
        symbols.append(symbol)
        return _Ticker(calls)

    client = NativeHistoryClient(tmp_path / "shared", ticker_factory=ticker)
    intent = YfinanceDailyIntent(
        symbol="7203.T",
        jpx_code="7203",
        jpx_snapshot_on="2026-09-01",
        mic="XTKS",
        market_segment="Prime",
        start="2026-09-01",
        end="2026-09-26",
    )
    previous = YfConfig.network.retries, YfConfig.debug.hide_exceptions
    adapter = YfinanceDailyAdapter(history_client=client)
    pd.testing.assert_frame_equal(adapter.download(intent), _frame())
    assert symbols == ["7203.T"]
    assert calls == [
        {
            "start": "2026-09-01",
            "end": "2026-09-26",
            "interval": "1d",
            "auto_adjust": False,
            "actions": True,
            "repair": False,
            "keepna": True,
            "timeout": 10,
        }
    ]
    assert adapter.metadata(intent).function == "Ticker.history"
    assert (YfConfig.network.retries, YfConfig.debug.hide_exceptions) == previous


@pytest.mark.parametrize(
    "cached",
    [
        None,
        {},
        {
            "currency": "JPY",
            "exchangeTimezoneName": "Asia/Tokyo",
            "symbol": "7203.T",
            "instrumentType": "EQUITY",
            "secret": "canary",
        },
    ],
)
def test_evidence_acquisition_reads_cached_metadata_without_lazy_calls(tmp_path: Path, cached: object) -> None:
    """Retain allowlisted observed metadata with no metadata getter or second history call."""
    calls: list[dict[str, object]] = []

    class CachedTicker(_Ticker):
        def history(self, **kwargs: object) -> object:
            self._price_history = SimpleNamespace(_history_metadata=cached)
            return super().history(**kwargs)

        @property
        def history_metadata(self) -> object:
            pytest.fail("lazy getter invoked")

        def get_history_metadata(self) -> object:
            pytest.fail("lazy getter invoked")

    client = NativeHistoryClient(tmp_path / "state", ticker_factory=lambda _: CachedTicker(calls), clock=lambda: 1000)
    intent = YfinanceDailyIntent(
        symbol="7203.T",
        jpx_code="7203",
        jpx_snapshot_on="2026-08-31",
        mic="XTKS",
        market_segment="Prime",
        start="2023-09-26",
        end="2026-09-26",
    )
    result = YfinanceDailyAdapter(history_client=client).acquire_history(intent)
    assert len(calls) == 1
    assert result.metadata["retrieved_at"] == "1970-01-01T00:16:40+00:00"
    assert result.metadata["currency"] == ("JPY" if cached else None)
    assert "canary" not in json.dumps(result.metadata)
    if isinstance(cached, dict):
        cached["currency"] = "USD"
        assert result.metadata["currency"] != "USD"


def test_spacing_is_persistent_and_measured_after_completion(tmp_path: Path) -> None:
    """Reconstructing the client does not bypass the provider deadline."""
    clock = [1000.0]
    calls: list[dict[str, object]] = []

    class SlowTicker(_Ticker):
        def history(self, **kwargs: object) -> object:
            clock[0] = 1010.0
            return super().history(**kwargs)

    root = tmp_path / "shared"
    NativeHistoryClient(root, ticker_factory=lambda _: SlowTicker(calls), clock=lambda: clock[0]).history(
        "7203.T", period="1y"
    )
    other = NativeHistoryClient(root, ticker_factory=lambda _: _Ticker(calls), clock=lambda: clock[0])
    clock[0] = 1019.0
    with pytest.raises(HistoryThrottledError) as error:
        other.history("1301.T", period="1y")
    assert error.value.retry_at == 1020.0
    assert len(calls) == 1
    clock[0] = 1020.0
    other.history("1301.T", period="1y")
    assert len(calls) == 2


@pytest.mark.parametrize(
    "failure,reason",
    [(YFRateLimitError(), "yfinance_rate_limited"), (RuntimeError("secret-canary"), "yfinance_history_failed")],
)
def test_errors_persist_cooldown_without_retry(tmp_path: Path, failure: Exception, reason: str) -> None:
    """Provider failures cannot trigger immediate retries or expose exception text."""
    calls: list[dict[str, object]] = []
    root = tmp_path / "shared"
    client = NativeHistoryClient(root, ticker_factory=lambda _: _Ticker(calls, failure), clock=lambda: 1000.0)
    with pytest.raises(HistoryAcquisitionError, match=reason):
        client.history("7203.T", period="1y")
    other = NativeHistoryClient(root, ticker_factory=lambda _: _Ticker(calls), clock=lambda: 1010.0)
    with pytest.raises(HistoryThrottledError) as error:
        other.history("7203.T", period="1y")
    assert error.value.retry_at == 1900.0
    assert len(calls) == 1
    assert "secret-canary" not in (root / "provider.json").read_text()


def _hold_gate(root: Path, ready: Any, finish: Any) -> None:
    with NativeHistoryClient(root, clock=lambda: 1000.0)._admit():
        ready.set()
        finish.wait(10)


def test_process_lock_and_crash_reservation(tmp_path: Path) -> None:
    """A separate process cannot overlap, and a crash leaves a cooldown."""
    context = multiprocessing.get_context("spawn")
    ready, finish = context.Event(), context.Event()
    root = tmp_path / "shared"
    child = context.Process(target=_hold_gate, args=(root, ready, finish))
    child.start()
    try:
        assert ready.wait(10)
        with (
            pytest.raises(HistoryThrottledError, match="yfinance_busy"),
            NativeHistoryClient(root, clock=lambda: 1001.0)._admit(),
        ):
            pytest.fail("overlapping call")
        child.terminate()
        child.join(10)
        with (
            pytest.raises(HistoryThrottledError, match="yfinance_cooldown"),
            NativeHistoryClient(root, clock=lambda: 2002.0)._admit(),
        ):
            pytest.fail("crash bypass")
    finally:
        if child.is_alive():
            child.terminate()
        child.join(10)


@pytest.mark.parametrize("state", ["broken", '{"version":2}', '{"version":1,"recorded_at":1001,"next_at":1011}'])
def test_invalid_state_and_clock_rollback_block_before_network(tmp_path: Path, state: str) -> None:
    """Unknown state or backward clock movement never resets the budget."""
    root = tmp_path / "shared"
    root.mkdir(mode=0o700)
    (root / "provider.json").write_text(state)
    client = NativeHistoryClient(root, ticker_factory=lambda _: pytest.fail("network"), clock=lambda: 1000.0)
    with pytest.raises(HistoryAcquisitionError):
        client.history("7203.T", period="1y")


@pytest.mark.parametrize("frame", [pd.DataFrame(), pd.DataFrame({"Close": [1]})])
def test_empty_or_malformed_data_is_not_success(tmp_path: Path, frame: pd.DataFrame) -> None:
    """No successful result is fabricated from invalid library output."""

    class InvalidTicker:
        def history(self, **kwargs: object) -> object:
            return frame

    client = NativeHistoryClient(tmp_path / "shared", ticker_factory=lambda _: InvalidTicker())
    with pytest.raises(HistoryAcquisitionError):
        client.history("7203.T", period="1y")
    state = json.loads((tmp_path / "shared/provider.json").read_text())
    assert state["next_at"] - state["recorded_at"] == 900


def test_cli_persists_prices_and_data_provenance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Evidence is a returned table with its hash, without HTTP material."""
    import hashlib
    import sys

    from stock_research_llm_orchestrator.sources.yfinance import history_cli

    calls: list[dict[str, object]] = []
    client = NativeHistoryClient(tmp_path / "shared", ticker_factory=lambda _: _Ticker(calls))
    monkeypatch.setattr(history_cli, "NativeHistoryClient", lambda: client)
    output = tmp_path / "result"
    monkeypatch.setattr(sys, "argv", ["history_cli", "--symbol", "7203.T", "--output", str(output)])
    history_cli.main()
    metadata = json.loads((output / "metadata.json").read_text())
    assert metadata["sha256"] == hashlib.sha256((output / "prices.csv").read_bytes()).hexdigest()
    assert metadata["function"] == "Ticker.history"
    assert metadata["arguments"]["period"] == "1y"
    assert metadata["timezone"] == "Asia/Tokyo"
    assert metadata["http_body_retained"] is False
    assert metadata["jpx_eligibility_verified"] is False
    assert len(calls) == 1
    with pytest.raises(FileExistsError):
        history_cli.main()
    assert len(calls) == 1


def test_source_authorization_failure_prevents_invocation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Relaxing HTTP controls does not relax source authorization."""
    from stock_research_llm_orchestrator.sources.yfinance import native_history

    def reject() -> None:
        raise HistoryAcquisitionError("yfinance_source_not_approved")

    monkeypatch.setattr(native_history, "_validate_approval", reject)
    client = NativeHistoryClient(tmp_path / "shared", ticker_factory=lambda _: pytest.fail("network"))
    with pytest.raises(HistoryAcquisitionError, match="yfinance_source_not_approved"):
        client.history("7203.T", period="1y")
    assert not (tmp_path / "shared").exists()
