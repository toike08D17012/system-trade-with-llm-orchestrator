"""Price quality and calendar boundaries for deterministic native normalization."""

import json
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pandas as pd  # type: ignore[import-untyped]
import pytest

from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.evidence import ApplicablePeriodV1
from stock_research_llm_orchestrator.sources.yfinance.normalization import (
    COLUMNS,
    TradingDates,
    normalize_history,
    three_year_start,
)


START, END = date(2023, 9, 26), date(2026, 9, 26)


def _frame() -> pd.DataFrame:
    fixture = json.loads((Path(__file__).parents[1] / "fixtures/market_evidence/series.json").read_bytes())
    days = pd.date_range(START, END - timedelta(days=1), freq="B", tz="Asia/Tokyo")
    return pd.DataFrame({column: fixture[column] for column in COLUMNS}, index=days)


def _calendar(frame: pd.DataFrame) -> TradingDates:
    return TradingDates(
        period=ApplicablePeriodV1(start_date=START.isoformat(), end_date=(END - timedelta(days=1)).isoformat()),
        dates=tuple(stamp.date().isoformat() for stamp in frame.index),
    )


def test_three_year_normalization_preserves_provider_values_and_units() -> None:
    """Normalize actual values without rounding or deriving adjusted OHLC."""
    frame = _frame()
    frame.loc[frame.index[5], "Dividends"] = 2.5
    frame.loc[frame.index[10], "Stock Splits"] = 3.0
    result = normalize_history(frame, start=START, end=END, currency="JPY", trading_dates=_calendar(frame))
    assert result.quality_passed
    assert result.rows[0].open == Decimal("100.25")
    assert result.rows[0].adjusted_close == Decimal("52.75")
    assert result.rows[0].stock_splits == 0
    assert result.rows[10].stock_splits == 3
    assert result.rows[5].dividends == Decimal("2.5")
    assert result.requested_period.end_date == "2026-09-25"
    assert result.model_dump(mode="json")["rows"][0]["open"] == "100.25"


@pytest.mark.parametrize(
    "column,value,issue",
    [
        ("Close", float("nan"), "missing_value"),
        ("Volume", float("inf"), "nonfinite_value"),
        ("Volume", -1.0, "invalid_sign"),
        ("Close", 0.0, "invalid_sign"),
        ("Volume", 1.5, "fractional_volume"),
        ("Dividends", -1.0, "invalid_sign"),
        ("Stock Splits", -2.0, "invalid_sign"),
        ("High", 80.0, "ohlc_conflict"),
    ],
)
def test_numeric_quality_gaps_are_not_imputed(column: str, value: float, issue: str) -> None:
    """Keep source zeros distinct from nulls and report invalid numeric semantics."""
    frame = _frame().astype(float)
    frame.loc[frame.index[0], column] = value
    result = normalize_history(frame, start=START, end=END, currency="JPY", trading_dates=_calendar(frame))
    assert issue in {item.code for item in result.issues}
    assert not result.quality_passed
    if issue in {"missing_value", "nonfinite_value"}:
        assert getattr(result.rows[0], "close" if column == "Close" else "volume") is None


@pytest.mark.parametrize("currency,code", [(None, "currency_unknown"), ("USD", "currency_conflict")])
def test_currency_requires_observed_metadata(currency: str | None, code: str) -> None:
    """A Tokyo price index never substitutes for observed currency."""
    result = normalize_history(_frame(), start=START, end=END, currency=currency)
    assert code in {issue.code for issue in result.issues}
    assert result.trading_day_coverage == "unconfirmed"
    assert not result.quality_passed


def test_missing_trading_day_and_short_request_are_separate() -> None:
    """A long date span does not prove day completeness or satisfy a shorter request."""
    full = _frame()
    result = normalize_history(
        full.drop(full.index[100]), start=START, end=END, currency="JPY", trading_dates=_calendar(full)
    )
    assert result.three_year_request
    assert result.trading_day_coverage == "incomplete"
    assert {issue.code for issue in result.issues} == {"missing_trading_day"}
    short = normalize_history(full.iloc[-5:], start=date(2026, 9, 21), end=END, currency="JPY")
    assert not short.three_year_request
    assert not short.quality_passed


@pytest.mark.parametrize("case", ["naive", "duplicate", "reverse", "end_date", "duplicate_column"])
def test_invalid_date_or_column_structure_is_rejected(case: str) -> None:
    """Never collapse duplicate rows or reinterpret timezone-free dates."""
    frame = _frame()
    if case == "naive":
        frame.index = frame.index.tz_localize(None)
    elif case == "duplicate":
        frame = pd.concat([frame, frame.iloc[-1:]])
    elif case == "reverse":
        frame = frame.iloc[::-1]
    elif case == "end_date":
        frame = frame.iloc[-1:].copy()
        frame.index = pd.DatetimeIndex([pd.Timestamp(END, tz="Asia/Tokyo")])
    else:
        frame = pd.concat([frame, frame[["Close"]]], axis=1)
    with pytest.raises(ValueError):
        normalize_history(frame, start=START, end=END, currency="JPY")


def test_timezone_conversion_and_leap_boundary_are_explicit() -> None:
    """Equivalent UTC instants retain the Tokyo trading date; leap years are calendar years."""
    frame = _frame()
    expected = normalize_history(frame, start=START, end=END, currency="JPY")
    frame.index = frame.index.tz_convert("UTC")
    actual = normalize_history(frame, start=START, end=END, currency="JPY")
    assert actual.rows == expected.rows
    assert actual.source_timezone == "UTC"
    assert three_year_start(date(2024, 2, 29)) == date(2021, 2, 28)
    assert three_year_start(date(2026, 9, 26)) == START


def test_unexpected_day_and_incomplete_calendar_do_not_pass() -> None:
    """Require evidence for the entire requested calendar range."""
    frame = _frame()
    calendar = _calendar(frame)
    missing = calendar.model_copy(update={"dates": calendar.dates[1:]})
    result = normalize_history(frame, start=START, end=END, currency="JPY", trading_dates=missing)
    assert "unexpected_trading_day" in {issue.code for issue in result.issues}
    short = calendar.model_copy(update={"period": ApplicablePeriodV1(start_date="2023-09-27", end_date="2026-09-25")})
    result = normalize_history(frame, start=START, end=END, currency="JPY", trading_dates=short)
    assert result.trading_day_coverage == "unconfirmed"
    assert not result.quality_passed
