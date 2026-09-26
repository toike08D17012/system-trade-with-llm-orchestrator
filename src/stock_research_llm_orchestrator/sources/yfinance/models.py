"""Strict source-native contracts for approved yfinance daily downloads."""

from datetime import date
from typing import Literal

from pydantic import Field, model_validator

from stock_research_llm_orchestrator.contracts.base import StrictContractModel


class YfinanceDailyIntent(StrictContractModel):
    """One verified Tokyo-listed security and an exact daily date range."""

    symbol: str = Field(pattern=r"^[0-9A-Z]{4}\.T$")
    jpx_code: str = Field(pattern=r"^[0-9A-Z]{4}$")
    jpx_snapshot_on: str
    mic: Literal["XTKS"]
    market_segment: Literal["Prime", "Standard", "Growth"]
    start: str
    end: str

    @model_validator(mode="after")
    def validate_identity_and_period(self) -> YfinanceDailyIntent:
        """Require exact symbol derivation and an inclusive/exclusive date range."""
        date.fromisoformat(self.jpx_snapshot_on)
        start = date.fromisoformat(self.start)
        end = date.fromisoformat(self.end)
        if self.symbol != f"{self.jpx_code}.T":
            raise ValueError("yfinance_symbol_identity_mismatch")
        if start >= end:
            raise ValueError("invalid_yfinance_daily_period")
        return self


class YfinanceDownloadMetadata(StrictContractModel):
    """Reproducibility metadata for the pinned source invocation."""

    library: Literal["yfinance"] = "yfinance"
    library_version: Literal["1.7.0"] = "1.7.0"
    function: Literal["download", "Ticker.history"] = "download"
    symbol: str = Field(pattern=r"^[0-9A-Z]{4}\.T$")
    start: str
    end: str
    interval: Literal["1d"] = "1d"
    threads: Literal[False] | None = False
    repair: Literal[False] = False
    keepna: Literal[True] = True
    progress: Literal[False] | None = False
    actions: Literal[True] = True
    auto_adjust: Literal[False] = False
    jpx_snapshot_on: str
