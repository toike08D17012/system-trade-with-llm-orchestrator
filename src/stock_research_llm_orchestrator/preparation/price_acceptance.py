"""Accept fixed-period price evidence under the approved local acceptance policy."""

import csv
import io
import json
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

import yaml
from pydantic import Field

from stock_research_llm_orchestrator.contracts.base import (
    FileReferenceV1,
    Identifier,
    NonEmptyString,
    Sha256Hex,
    StrictContractModel,
    Timestamp,
)
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.policies import SourceApprovalV1
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.task import DetailedAnalysisTaskV1
from stock_research_llm_orchestrator.preparation.market_evidence import (
    JpxSnapshotProvenance,
    MarketPreparationIndex,
    validate_market_preparation,
)
from stock_research_llm_orchestrator.preparation.market_revalidation import (
    ResearchCalendarMetadata,
    _read_file,
    _safe_path,
    _timestamp,
)
from stock_research_llm_orchestrator.preparation.storage import publish_preparation
from stock_research_llm_orchestrator.sources.yfinance.mapping import map_jpx_verification_to_yfinance_daily
from stock_research_llm_orchestrator.sources.yfinance.normalization import (
    COLUMNS,
    NormalizedPrices,
    PriceQualityIssue,
    TradingDates,
    normalize_price_row,
    three_year_start,
)


AcceptanceStatus = Literal["accepted", "accepted_with_limitations", "pending"]
CONDITION_IDS = tuple(f"PA-{number:02d}" for number in range(1, 10))
LIMITATIONS = (
    "current_listing_and_latest_publication_unconfirmed",
    "scheduled_calendar_only",
    "provider_table_not_http_body",
    "provider_adjusted_close_only",
    "dividend_currency_not_independently_verified",
    "no_independent_provider_comparison",
    "fixed_period_not_current_run_freshness",
)
RESTRICTED_USES = (
    "Do not claim current listing eligibility, live freshness, or independently verified prices.",
    "Do not compute adjusted OHLC from these inputs.",
    "Defer dividend reinvestment or total-return calculations requiring verified dividend currency.",
    "Recheck the required terminal date before use in a new analysis run.",
    "Keep retained source bytes local; this receipt grants no external transfer or publication permission.",
)


class KnownPriceConflict(StrictContractModel):
    """One supplied material conflict bound to a local supporting document."""

    evidence_id: Identifier
    source_reference: NonEmptyString
    checked_on: str
    security_code: str = Field(pattern=r"^[0-9A-Z]{4}$")
    category: Literal["delisted", "outside_target_market", "identity", "currency", "dates", "values", "provenance"]
    evidence_sha256: Sha256Hex


class AcceptanceCondition(StrictContractModel):
    """One reproducible policy condition with reasons and retained evidence references."""

    condition_id: str = Field(pattern=r"^PA-0[1-9]$")
    passed: bool
    reasons: tuple[str, ...]
    evidence_paths: tuple[str, ...]


class PriceAcceptanceIndex(StrictContractModel):
    """Internal acceptance receipt for one fixed period, not analysis readiness."""

    version: Literal[1] = 1
    kind: Literal["internal-price-evidence-acceptance"] = "internal-price-evidence-acceptance"
    acceptance_policy: Literal["price-evidence-acceptance-v1"] = "price-evidence-acceptance-v1"
    scope: Literal["fixed_period_local_internal_analysis"] = "fixed_period_local_internal_analysis"
    checked_at: Timestamp
    task_id: Identifier
    evaluation_policy_version: int = Field(ge=1)
    status: AcceptanceStatus
    analysis_ready: Literal[False] = False
    period_start: str
    period_end: str
    observed_count: int
    missing_dates: tuple[str, ...]
    unexpected_dates: tuple[str, ...]
    conditions: tuple[AcceptanceCondition, ...]
    limitations: tuple[str, ...]
    restricted_uses: tuple[str, ...]
    original_issues: tuple[PriceQualityIssue, ...]
    files: tuple[FileReferenceV1, ...]


def acceptance_status(conditions: tuple[AcceptanceCondition, ...], limitations: tuple[str, ...]) -> AcceptanceStatus:
    """Require exactly nine successful conditions before any acceptance."""
    if tuple(condition.condition_id for condition in conditions) != CONDITION_IDS:
        raise ValueError("invalid_acceptance_condition_set")
    if not all(condition.passed for condition in conditions):
        return "pending"
    return "accepted_with_limitations" if limitations else "accepted"


def _csv_quality(body: bytes, normalized: NormalizedPrices) -> tuple[tuple[str, ...], list[PriceQualityIssue], bool]:
    """Read decimal source strings and verify stored values without a float round trip."""
    reader = csv.reader(io.StringIO(body.decode("utf-8")), strict=True)
    header = next(reader, [])
    if (
        len(header) != len(set(header))
        or not set(COLUMNS).issubset(header)
        or header[0] not in {"", "Date", "Datetime"}
    ):
        raise ValueError("invalid_price_csv_columns")
    positions = tuple(header.index(column) for column in COLUMNS)
    rows = []
    issues: list[PriceQualityIssue] = []
    daily = True
    for fields in reader:
        if len(fields) != len(header):
            raise ValueError("invalid_price_csv_row")
        stamp = _timestamp(fields[0])
        local = stamp.astimezone(ZoneInfo("Asia/Tokyo"))
        daily = daily and not any((local.hour, local.minute, local.second, local.microsecond))
        daily = daily and stamp.utcoffset() == local.utcoffset()
        values = tuple(fields[position] if fields[position] else None for position in positions)
        rows.append(normalize_price_row(local.date().isoformat(), values, issues))
    if tuple(rows) != normalized.rows:
        raise ValueError("source_normalized_values_mismatch")
    return tuple(row.on for row in rows), issues, daily


def _approval_valid(body: bytes, source: str, version: int, today: str, acquired: str) -> bool:
    approval = SourceApprovalV1.model_validate_json(json.dumps(yaml.safe_load(body)))
    return (
        approval.source_id == source
        and approval.approval_version == version
        and approval.status == "approved"
        and approval.online_use_allowed
        and approval.usage_entity == "private_individual_internal_analysis"
        and approval.effective_on is not None
        and approval.effective_on <= acquired <= today
        and approval.recheck_due_on is not None
        and today <= approval.recheck_due_on
    )


def _evaluate(files: Mapping[str, bytes], checked_at: str) -> PriceAcceptanceIndex:
    now = _timestamp(checked_at)
    today = now.astimezone(ZoneInfo("Asia/Tokyo")).date().isoformat()
    prepared = {
        name.removeprefix("preparation/"): body for name, body in files.items() if name.startswith("preparation/")
    }
    validate_market_preparation(prepared)
    original = MarketPreparationIndex.model_validate_json(prepared["index.json"])
    task = DetailedAnalysisTaskV1.model_validate_json(prepared["task.json"])
    normalized = NormalizedPrices.model_validate_json(prepared["normalized.json"])
    provenance = JpxSnapshotProvenance.model_validate_json(prepared["jpx-metadata.json"])
    metadata = json.loads(prepared["history-metadata.json"])
    if not isinstance(metadata, dict):
        raise ValueError("invalid_history_metadata")
    if original.normalized_evidence.data_version != sha256(prepared["prices.csv"]).hexdigest():
        raise ValueError("normalized_source_version_mismatch")
    retrieved = _timestamp(metadata["retrieved_at"])
    if max(retrieved, _timestamp(original.prepared_at), _timestamp(provenance.retrieved_at)) > now:
        raise ValueError("future_preparation_timestamp")
    period = normalized.requested_period
    start = date.fromisoformat(period.start_date)
    end = date.fromisoformat(period.end_date) + timedelta(days=1)
    if end.isoformat() > today:
        raise ValueError("price_period_includes_unfinished_day")
    if original.jpx_verification.snapshot_on > _timestamp(provenance.retrieved_at).astimezone(
        ZoneInfo("Asia/Tokyo")
    ).date().isoformat() or retrieved > _timestamp(original.prepared_at):
        raise ValueError("inconsistent_source_chronology")
    symbol = f"{task.security.security_code}.T"
    try:
        intent = map_jpx_verification_to_yfinance_daily(original.jpx_verification, start=start, end=end)
        identity = intent.symbol == symbol and task.security.mic == "XTKS"
    except ValueError:
        identity = False
    identity = identity and metadata.get("symbol") == symbol and metadata.get("response_symbol") == symbol
    identity = identity and metadata.get("instrument_type") == "EQUITY"
    days, numeric_issues, daily = _csv_quality(prepared["prices.csv"], normalized)
    calendar_metadata = ResearchCalendarMetadata.model_validate_json(files["calendar-metadata.json"])
    if (
        calendar_metadata.calendar_sha256 != sha256(files["calendar.json"]).hexdigest()
        or calendar_metadata.research_note_sha256 != sha256(files["research-note.md"]).hexdigest()
        or _timestamp(calendar_metadata.checked_at) > now
    ):
        raise ValueError("invalid_calendar_provenance")
    calendar = TradingDates.model_validate_json(files["calendar.json"])
    covers = calendar.period.start_date <= period.start_date and calendar.period.end_date >= period.end_date
    expected = {day for day in calendar.dates if period.start_date <= day <= period.end_date}
    observed = set(days)
    missing, unexpected = tuple(sorted(expected - observed)), tuple(sorted(observed - expected))
    date_valid = bool(days) and days == tuple(sorted(observed)) and daily
    date_valid = date_valid and all(period.start_date <= day <= period.end_date for day in days)
    arguments = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "interval": "1d",
        "auto_adjust": False,
        "actions": True,
        "repair": False,
        "keepna": True,
        "timeout": 10,
    }
    expected_metadata = {
        "source": "Yahoo Finance",
        "library": "yfinance",
        "library_version": "1.7.0",
        "function": "Ticker.history",
        "policy": "yfinance-native-history-v1",
        "source_approval_version": 3,
        "rate_unit": "library_call",
        "http_body_retained": False,
        "market_metadata_origin": "cached-history-chart-meta",
        "arguments": arguments,
        "artifact_kind": "yfinance_returned_dataframe",
        "dividend_currency_verification": "not_independently_verified",
    }
    source_valid = all(
        json.dumps(metadata.get(key), sort_keys=True) == json.dumps(value, sort_keys=True)
        for key, value in expected_metadata.items()
    )
    currency_valid = (
        metadata.get("currency") == normalized.currency == "JPY"
        and metadata.get("exchange_timezone") == normalized.source_timezone == "Asia/Tokyo"
    )
    for source, version, acquired in (
        ("jpx", 1, _timestamp(provenance.retrieved_at)),
        ("yfinance", 3, retrieved),
    ):
        approval_valid = _approval_valid(
            files[f"approvals/{source}.yaml"],
            source,
            version,
            today,
            acquired.astimezone(ZoneInfo("Asia/Tokyo")).date().isoformat(),
        )
        source_valid = source_valid and approval_valid
    conflict_reasons = []
    if "known-conflict.json" in files:
        conflict = KnownPriceConflict.model_validate_json(files["known-conflict.json"])
        if (
            date.fromisoformat(conflict.checked_on).isoformat() != conflict.checked_on
            or conflict.checked_on > today
            or conflict.security_code != task.security.security_code
            or conflict.evidence_sha256 != sha256(files["conflict-evidence.bin"]).hexdigest()
        ):
            raise ValueError("invalid_conflict_provenance")
        conflict_reasons.append(f"known_conflict:{conflict.category}")
    # Only known, freshly reevaluated checks or explicitly permitted gaps may be replaced.
    reevaluated = {
        "trading_dates_unconfirmed",
        "jpx_snapshot_freshness_unconfirmed",
        "missing_trading_day",
        "unexpected_trading_day",
        "trading_calendar_insufficient_period",
        "less_than_three_year_request",
        "empty_prices",
        "non_daily_timestamp",
        "currency_unknown",
        "currency_conflict",
        "provider_identity_unconfirmed",
        "provider_identity_conflict",
        "missing_value",
        "invalid_number",
        "nonfinite_value",
        "invalid_sign",
        "fractional_volume",
        "ohlc_conflict",
    }
    conflict_reasons.extend(
        f"unrecognized_original_issue:{issue.code}"
        for issue in (*original.issues, *normalized.issues)
        if issue.code not in reevaluated
    )
    conditions = []

    def condition(number: int, passed: bool, reasons: tuple[str, ...], evidence: tuple[str, ...]) -> None:
        conditions.append(
            AcceptanceCondition(
                condition_id=f"PA-{number:02d}",
                passed=passed,
                reasons=() if passed else reasons,
                evidence_paths=evidence,
            )
        )

    condition(
        1,
        identity,
        ("security_identity_not_verified",),
        ("preparation/jpx.xlsx", "preparation/task.json", "preparation/history-metadata.json"),
    )
    condition(
        2,
        source_valid,
        ("source_approval_or_acquisition_policy_not_valid",),
        ("approvals/jpx.yaml", "approvals/yfinance.yaml", "preparation/history-metadata.json"),
    )
    condition(
        3,
        bool(days) and start <= three_year_start(end) and covers and not missing,
        ("three_year_period_or_full_coverage_missing",),
        ("preparation/normalized.json", "calendar.json"),
    )
    condition(
        4,
        date_valid and covers and not missing and not unexpected,
        ("dates_or_calendar_coverage_invalid",),
        ("preparation/prices.csv", "calendar.json"),
    )
    condition(
        5,
        bool(days) and not numeric_issues,
        tuple(sorted({issue.code for issue in numeric_issues})) or ("empty_prices",),
        ("preparation/prices.csv", "preparation/normalized.json"),
    )
    condition(
        6,
        currency_valid,
        ("currency_or_timezone_unconfirmed_or_conflicting",),
        ("preparation/history-metadata.json", "preparation/normalized.json"),
    )
    condition(
        7,
        metadata.get("arguments") == arguments,
        ("adjustment_or_action_arguments_invalid",),
        ("preparation/history-metadata.json", "preparation/normalized.json"),
    )
    condition(8, True, (), tuple(sorted(files)))
    condition(
        9,
        not conflict_reasons
        and identity
        and currency_valid
        and not numeric_issues
        and date_valid
        and not missing
        and not unexpected,
        tuple(conflict_reasons) or ("material_input_conflict",),
        ("known-conflict.json", "conflict-evidence.bin")
        if "known-conflict.json" in files
        else ("preparation/index.json",),
    )
    results = tuple(conditions)
    references = tuple(
        FileReferenceV1(
            artifact_id=name.replace("/", ":"),
            relative_path=name,
            media_type="application/octet-stream",
            sha256=sha256(body).hexdigest(),
        )
        for name, body in sorted(files.items())
    )
    return PriceAcceptanceIndex(
        checked_at=checked_at,
        task_id=task.task_id,
        evaluation_policy_version=task.evaluation_policy_version,
        status=acceptance_status(results, LIMITATIONS),
        period_start=period.start_date,
        period_end=period.end_date,
        observed_count=len(days),
        missing_dates=missing,
        unexpected_dates=unexpected,
        conditions=results,
        limitations=LIMITATIONS,
        restricted_uses=RESTRICTED_USES,
        original_issues=original.issues,
        files=references,
    )


def validate_price_acceptance(files: Mapping[str, bytes]) -> None:
    """Reproduce the entire receipt from its retained source and policy inputs."""
    index = PriceAcceptanceIndex.model_validate_json(files["index.json"])
    inputs = {name: body for name, body in files.items() if name != "index.json"}
    required = {
        "calendar.json",
        "calendar-metadata.json",
        "research-note.md",
        "approvals/jpx.yaml",
        "approvals/yfinance.yaml",
    }
    support = {name for name in inputs if not name.startswith("preparation/")}
    if support not in (required, required | {"known-conflict.json", "conflict-evidence.bin"}):
        raise ValueError("invalid_acceptance_input_set")
    if index != _evaluate(inputs, index.checked_at):
        raise ValueError("price_acceptance_receipt_mismatch")


def accept_price_evidence(
    *,
    preparation: Path,
    calendar: Path,
    calendar_metadata: Path,
    research_note: Path,
    output: Path,
    checked_at: datetime,
    approval_root: Path = Path("config/source-approvals"),
    known_conflict: Path | None = None,
    conflict_evidence: Path | None = None,
) -> PriceAcceptanceIndex:
    """Accept local fixed-period evidence; caller exclusively controls all paths."""
    if (known_conflict is None) != (conflict_evidence is None):
        raise ValueError("conflict_inputs_must_be_paired")
    source, destination = _safe_path(preparation), _safe_path(output)
    paths = {
        "calendar.json": calendar,
        "calendar-metadata.json": calendar_metadata,
        "research-note.md": research_note,
        "approvals/jpx.yaml": approval_root / "jpx/v1.yaml",
        "approvals/yfinance.yaml": approval_root / "yfinance/v3.yaml",
    }
    if known_conflict is not None and conflict_evidence is not None:
        paths.update({"known-conflict.json": known_conflict, "conflict-evidence.bin": conflict_evidence})
    inputs = [source, *(_safe_path(path) for path in paths.values())]
    if any(destination == path or destination in path.parents or path in destination.parents for path in inputs):
        raise ValueError("overlapping_acceptance_paths")
    if destination.exists():
        raise FileExistsError("acceptance_destination_exists")
    files = {f"preparation/{path.name}": _read_file(path) for path in sorted(source.iterdir())}
    files.update({name: _read_file(path) for name, path in paths.items()})
    result = _evaluate(files, checked_at.isoformat())
    files["index.json"] = result.model_dump_json(indent=2).encode() + b"\n"
    publish_preparation(destination.parent, destination.name, files, validator=validate_price_acceptance)
    return result
