"""Pinned, dependency-injected yfinance daily download invocation."""

import logging
from collections.abc import Callable
from threading import RLock
from typing import Protocol

import yfinance as yf  # type: ignore[import-untyped]
from yfinance.config import YfConfig  # type: ignore[import-untyped]

from stock_research_llm_orchestrator.requests.production import LogicalResultOutcome
from stock_research_llm_orchestrator.sources.yfinance.coordinated_session import CoordinatedSession
from stock_research_llm_orchestrator.sources.yfinance.models import YfinanceDailyIntent, YfinanceDownloadMetadata
from stock_research_llm_orchestrator.sources.yfinance.native_history import NativeHistoryClient


_YF_CONFIG_LOCK = RLock()


class _NetworkConfig(Protocol):
    retries: int


class _YfConfig(Protocol):
    network: _NetworkConfig


DownloadCallable = Callable[..., object]


class YfinanceDailyAdapter:
    """Invoke only the approved yfinance 1.7.0 daily-download surface."""

    def __init__(
        self,
        *,
        download: DownloadCallable | None = None,
        config: _YfConfig = YfConfig,
        library_version: str = yf.__version__,
        history_client: NativeHistoryClient | None = None,
    ) -> None:
        """Bind the pinned library and injectable call/config for offline tests."""
        if library_version != "1.7.0":
            raise ValueError("unsupported_yfinance_version")
        self._download = download
        self._config = config
        self._history_client = history_client

    def metadata(self, intent: YfinanceDailyIntent, *, legacy_session: bool = False) -> YfinanceDownloadMetadata:
        """Describe the exact pinned invocation without performing I/O."""
        return YfinanceDownloadMetadata(
            function="download" if legacy_session or self._download is not None else "Ticker.history",
            threads=False if legacy_session or self._download is not None else None,
            progress=False if legacy_session or self._download is not None else None,
            symbol=intent.symbol,
            start=intent.start,
            end=intent.end,
            jpx_snapshot_on=intent.jpx_snapshot_on,
        )

    def download(self, intent: YfinanceDailyIntent, *, session: CoordinatedSession | None = None) -> object:
        """Use native history by default; preserve explicit legacy session callers."""
        if session is None and self._download is None:
            snapshot = YfinanceDailyIntent.model_validate(intent.model_dump())
            client = self._history_client or NativeHistoryClient()
            return client.history(snapshot.symbol, start=snapshot.start, end=snapshot.end).frame
        if not isinstance(session, CoordinatedSession):
            raise ValueError("yfinance_session_required")
        if session.expected_symbol != intent.symbol:
            raise ValueError("yfinance_session_symbol_mismatch")
        session.assert_intent(intent)
        with _YF_CONFIG_LOCK:
            previous_retries = self._config.network.retries
            logger = logging.getLogger("yfinance")
            previous_level = logger.level
            self._config.network.retries = 0
            logger.setLevel(logging.WARNING)
            try:
                try:
                    value = (self._download or yf.download)(
                        tickers=[intent.symbol],
                        start=intent.start,
                        end=intent.end,
                        interval="1d",
                        threads=False,
                        repair=False,
                        keepna=True,
                        progress=False,
                        actions=True,
                        auto_adjust=False,
                        session=session,
                    )
                except Exception:
                    outcome = LogicalResultOutcome.UNKNOWN if session.uncertain else LogicalResultOutcome.FAILED
                    session.finalize(outcome, "yfinance_download_failed")
                    raise ValueError("yfinance_download_failed") from None
                if session.poisoned or not session.complete or value is None or getattr(value, "empty", False):
                    session.finalize(LogicalResultOutcome.FAILED, "yfinance_result_incomplete")
                    raise ValueError("yfinance_result_incomplete")
                session.finalize(LogicalResultOutcome.SUCCEEDED)
                return value
            finally:
                self._config.network.retries = previous_retries
                logger.setLevel(previous_level)
