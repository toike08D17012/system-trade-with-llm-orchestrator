"""Pinned native yfinance daily history acquisition."""

import yfinance as yf  # type: ignore[import-untyped]

from stock_research_llm_orchestrator.sources.yfinance.models import YfinanceDailyIntent, YfinanceDownloadMetadata
from stock_research_llm_orchestrator.sources.yfinance.native_history import HistoryResult, NativeHistoryClient


class YfinanceDailyAdapter:
    """Acquire one verified daily range through the supported native client."""

    def __init__(
        self, *, history_client: NativeHistoryClient | None = None, library_version: str = yf.__version__
    ) -> None:
        """Bind the pinned library and an optional offline test client."""
        if library_version != "1.7.0":
            raise ValueError("unsupported_yfinance_version")
        self._history_client = history_client

    def metadata(self, intent: YfinanceDailyIntent) -> YfinanceDownloadMetadata:
        """Describe the pinned native call without performing I/O."""
        return YfinanceDownloadMetadata(
            function="Ticker.history",
            threads=None,
            progress=None,
            symbol=intent.symbol,
            start=intent.start,
            end=intent.end,
            jpx_snapshot_on=intent.jpx_snapshot_on,
        )

    def download(self, intent: YfinanceDailyIntent) -> object:
        """Return the native history frame for existing data-preparation callers."""
        return self.acquire_history(intent).frame

    def acquire_history(self, intent: YfinanceDailyIntent) -> HistoryResult:
        """Retain native acquisition provenance for evidence preparation."""
        snapshot = YfinanceDailyIntent.model_validate(intent.model_dump())
        client = self._history_client or NativeHistoryClient()
        return client.history(snapshot.symbol, start=snapshot.start, end=snapshot.end)
