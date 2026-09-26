"""Resolve per-run price and UTC FX requirements from retained calendar bytes."""

from collections.abc import Mapping
from datetime import UTC, date, datetime, time, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

import yaml

from stock_research_llm_orchestrator.contracts.base import StrictContractModel
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.policies import MarketProfileV1
from stock_research_llm_orchestrator.preparation.market_revalidation import ResearchCalendarMetadata, _timestamp
from stock_research_llm_orchestrator.sources.yfinance.normalization import TradingDates, three_year_start


TOKYO = ZoneInfo("Asia/Tokyo")
PROFILE_EFFECTIVE_ON = date(2026, 9, 27)
PROFILE_RECHECK_ON = date(2026, 12, 27)
PROFILE_SHA256 = "3853b717f069a32b4a1789202a00937009e704e31695b282142e8048e8ab3414"


class RunRequirements(StrictContractModel):
    """Required dates remain independent from the availability of price/FX inputs."""

    version: Literal[1] = 1
    policy: Literal["price-fx-run-requirements-v1"] = "price-fx-run-requirements-v1"
    checked_at: str
    status: Literal["resolved", "pending"]
    required_price_end: str | None
    required_fx_end: str | None
    period_start: str
    period_end: str | None
    price_end_exclusive: str | None
    calendar_required_through: str
    price_dates: tuple[str, ...]
    fx_dates: tuple[str, ...]
    reasons: tuple[str, ...]


def session_end(day: str) -> datetime:
    """Use the approved current session time only within its effective period."""
    on = date.fromisoformat(day)
    if not PROFILE_EFFECTIVE_ON <= on < PROFILE_RECHECK_ON:
        raise ValueError("run_session_outside_profile_period")
    return datetime.combine(on, time(15, 30), TOKYO)


def calendar_inputs(config: Path, calendar: Path, metadata: Path, note: Path) -> dict[str, bytes]:
    """Read explicit inputs, refusing symlinks including parent components."""
    from stock_research_llm_orchestrator.preparation.market_revalidation import _read_file

    return {
        "market-profile.yaml": _read_file(config / "market-profiles/xtks/v2.yaml"),
        "calendar.json": _read_file(calendar),
        "calendar-metadata.json": _read_file(metadata),
        "research-note.md": _read_file(note),
    }


def resolve_requirements(files: Mapping[str, bytes], checked_at: str) -> RunRequirements:
    """Fail closed on calendar coverage; never infer weekends or provider arrival."""
    checked = _timestamp(checked_at)
    today = checked.astimezone(TOKYO).date()
    if sha256(files["market-profile.yaml"]).hexdigest() != PROFILE_SHA256:
        raise ValueError("run_market_profile_not_approved")
    profile = MarketProfileV1.model_validate(yaml.safe_load(files["market-profile.yaml"]))
    if not profile.enabled_for_runtime_date_resolution or profile.profile_version != 2:
        raise ValueError("run_market_profile_disabled")
    calendar = TradingDates.model_validate_json(files["calendar.json"])
    meta = ResearchCalendarMetadata.model_validate_json(files["calendar-metadata.json"])
    if (
        meta.calendar_sha256 != sha256(files["calendar.json"]).hexdigest()
        or meta.research_note_sha256 != sha256(files["research-note.md"]).hexdigest()
        or _timestamp(meta.checked_at) > checked
    ):
        raise ValueError("run_calendar_provenance_invalid")
    reasons = []
    if not PROFILE_EFFECTIVE_ON <= today < PROFILE_RECHECK_ON:
        reasons.append("market_profile_outside_effective_period")
    if calendar.period.end_date < today.isoformat():
        reasons.append("calendar_does_not_cover_check_date")
    # Before the current session closes, only earlier dates can be required.
    cutoff = today if checked >= datetime.combine(today, time(15, 30), TOKYO) else today - timedelta(days=1)
    completed = [day for day in calendar.dates if day <= cutoff.isoformat()]
    if not completed:
        reasons.append("calendar_has_no_completed_session")
    end = completed[-1] if completed else None
    start = (
        three_year_start(date.fromisoformat(end) + timedelta(days=1))
        if end
        else three_year_start(today + timedelta(days=1))
    )
    if calendar.period.start_date > start.isoformat():
        reasons.append("calendar_does_not_cover_required_start")
    if reasons:
        end = None
    price_dates = tuple(day for day in calendar.dates if end is not None and start.isoformat() <= day <= end)
    utc_completed = (checked.astimezone(UTC).date() - timedelta(days=1)).isoformat()
    fx_dates = tuple(day for day in price_dates if day <= utc_completed)
    return RunRequirements(
        checked_at=checked_at,
        status="pending" if reasons else "resolved",
        required_price_end=end,
        required_fx_end=fx_dates[-1] if fx_dates else None,
        period_start=start.isoformat(),
        period_end=end,
        price_end_exclusive=(date.fromisoformat(end) + timedelta(days=1)).isoformat() if end else None,
        calendar_required_through=today.isoformat(),
        price_dates=price_dates,
        fx_dates=fx_dates,
        reasons=tuple(reasons),
    )
