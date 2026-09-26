"""Deterministic daily-price normalization, without filling missing observations."""

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from numbers import Real
from typing import Literal

import pandas as pd  # type: ignore[import-untyped]
from pydantic import Field, model_validator

from stock_research_llm_orchestrator.contracts.base import StrictContractModel
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.evidence import ApplicablePeriodV1


COLUMNS = ("Open", "High", "Low", "Close", "Adj Close", "Volume", "Dividends", "Stock Splits")
FIELDS = ("open", "high", "low", "close", "adjusted_close", "volume", "dividends", "stock_splits")


class PriceQualityIssue(StrictContractModel):
    """A machine-readable problem, without source-controlled message text."""

    code: str
    on: str | None = None
    field: str | None = None


class TradingDates(StrictContractModel):
    """Caller-verified trading dates; source bytes are bound by the preparation layer."""

    period: ApplicablePeriodV1
    dates: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_dates(self) -> TradingDates:
        """Require unique, ordered ISO dates inside the declared covered period."""
        if self.dates != tuple(sorted(set(self.dates))):
            raise ValueError("invalid_trading_dates")
        for day in self.dates:
            if date.fromisoformat(day).isoformat() != day or not self.period.start_date <= day <= self.period.end_date:
                raise ValueError("invalid_trading_dates")
        return self


class DailyPrice(StrictContractModel):
    """Provider values with explicit nulls; Decimal values serialize as strings."""

    on: str
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal | None
    adjusted_close: Decimal | None
    volume: Decimal | None
    dividends: Decimal | None
    stock_splits: Decimal | None


class NormalizedPrices(StrictContractModel):
    """Internal versioned price slice, not a frozen analysis evidence set."""

    normalization_version: Literal[1] = 1
    numeric_policy: Literal["decimal-from-source-string-no-rounding"] = "decimal-from-source-string-no-rounding"
    adjustment_policy: Literal["provider-adjusted-close-only"] = "provider-adjusted-close-only"
    split_policy: Literal["provider-ratio-zero-means-no-event"] = "provider-ratio-zero-means-no-event"
    currency: str | None
    source_timezone: str | None
    timezone: Literal["Asia/Tokyo"] = "Asia/Tokyo"
    requested_period: ApplicablePeriodV1
    observed_period: ApplicablePeriodV1 | None
    three_year_request: bool
    trading_day_coverage: Literal["verified", "incomplete", "unconfirmed"]
    rows: tuple[DailyPrice, ...]
    issues: tuple[PriceQualityIssue, ...]

    @property
    def quality_passed(self) -> bool:
        """Only report success when the complete price slice was checked."""
        return not self.issues and self.three_year_request and self.trading_day_coverage == "verified"


def three_year_start(end_exclusive: date) -> date:
    """Return a calendar-year boundary, clamping leap day to February 28."""
    try:
        return end_exclusive.replace(year=end_exclusive.year - 3)
    except ValueError:
        return end_exclusive.replace(year=end_exclusive.year - 3, day=28)


def _number(value: object, on: str, field: str, issues: list[PriceQualityIssue]) -> Decimal | None:
    if value is None or value is pd.NA:
        issues.append(PriceQualityIssue(code="missing_value", on=on, field=field))
        return None
    if isinstance(value, bool) or not isinstance(value, (Real, Decimal, str)):
        issues.append(PriceQualityIssue(code="invalid_number", on=on, field=field))
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        issues.append(PriceQualityIssue(code="invalid_number", on=on, field=field))
        return None
    if not number.is_finite():
        code = "missing_value" if number.is_nan() else "nonfinite_value"
        issues.append(PriceQualityIssue(code=code, on=on, field=field))
        return None
    if number < 0 or (field in FIELDS[:5] and number == 0):
        issues.append(PriceQualityIssue(code="invalid_sign", on=on, field=field))
    if field == "volume" and number != number.to_integral_value():
        issues.append(PriceQualityIssue(code="fractional_volume", on=on, field=field))
    return number


def normalize_history(
    frame: pd.DataFrame,
    *,
    start: date,
    end: date,
    currency: str | None,
    trading_dates: TradingDates | None = None,
) -> NormalizedPrices:
    """Normalize an inclusive/exclusive daily request and preserve all quality gaps.

    An absent exchange calendar never becomes a weekday-calendar assumption.
    Structural failures raise; representable bad or missing numbers are retained
    with issues. No adjusted OHLC or corporate-action calculations are invented.
    """
    if start >= end:
        raise ValueError("invalid_price_period")
    if (
        not isinstance(frame, pd.DataFrame)
        or not isinstance(frame.index, pd.DatetimeIndex)
        or frame.columns.has_duplicates
        or not set(COLUMNS).issubset(frame.columns)
        or frame.index.hasnans
    ):
        raise ValueError("invalid_price_table")
    if frame.index.tz is None:
        raise ValueError("price_timezone_missing")
    source_timezone = str(frame.index.tz)
    index = frame.index.tz_convert("Asia/Tokyo")
    days = tuple(stamp.date().isoformat() for stamp in index)
    if days != tuple(sorted(set(days))):
        raise ValueError("price_dates_duplicate_or_unordered")
    if any(not start.isoformat() <= day < end.isoformat() for day in days):
        raise ValueError("price_date_outside_request")
    issues: list[PriceQualityIssue] = []
    if any(stamp.hour or stamp.minute or stamp.second or stamp.microsecond or stamp.nanosecond for stamp in index):
        issues.append(PriceQualityIssue(code="non_daily_timestamp"))
    if currency != "JPY":
        issues.append(PriceQualityIssue(code="currency_unknown" if currency is None else "currency_conflict"))
    enough = start <= three_year_start(end)
    if not enough:
        issues.append(PriceQualityIssue(code="less_than_three_year_request"))
    if not days:
        issues.append(PriceQualityIssue(code="empty_prices"))
    rows = []
    for day, values in zip(days, frame.loc[:, list(COLUMNS)].itertuples(index=False, name=None), strict=True):
        numbers = {field: _number(value, day, field, issues) for field, value in zip(FIELDS, values, strict=True)}
        low, high = numbers["low"], numbers["high"]
        if (
            low is not None
            and high is not None
            and (
                low > high
                or any(value is not None and not low <= value <= high for value in (numbers["open"], numbers["close"]))
            )
        ):
            issues.append(PriceQualityIssue(code="ohlc_conflict", on=day))
        rows.append(DailyPrice(on=day, **numbers))
    coverage: Literal["verified", "incomplete", "unconfirmed"] = "unconfirmed"
    period = ApplicablePeriodV1(start_date=start.isoformat(), end_date=(end - timedelta(days=1)).isoformat())
    if trading_dates is None:
        issues.append(PriceQualityIssue(code="trading_dates_unconfirmed"))
    elif trading_dates.period.start_date > period.start_date or trading_dates.period.end_date < period.end_date:
        issues.append(PriceQualityIssue(code="trading_calendar_insufficient_period"))
    else:
        expected = {day for day in trading_dates.dates if period.start_date <= day <= period.end_date}
        for day in sorted(expected - set(days)):
            issues.append(PriceQualityIssue(code="missing_trading_day", on=day))
        for day in sorted(set(days) - expected):
            issues.append(PriceQualityIssue(code="unexpected_trading_day", on=day))
        coverage = "verified" if expected == set(days) and days else "incomplete"
    observed = ApplicablePeriodV1(start_date=days[0], end_date=days[-1]) if days else None
    return NormalizedPrices(
        currency=currency,
        source_timezone=source_timezone,
        requested_period=period,
        observed_period=observed,
        three_year_request=enough,
        trading_day_coverage=coverage,
        rows=tuple(rows),
        issues=tuple(issues),
    )
