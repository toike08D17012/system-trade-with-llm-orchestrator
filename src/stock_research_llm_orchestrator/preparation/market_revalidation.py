"""Offline, immutable research revalidation of an existing market preparation."""

from collections.abc import Mapping
from datetime import date, datetime
from hashlib import sha256
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator

from stock_research_llm_orchestrator.contracts.base import (
    FileReferenceV1,
    Identifier,
    NonEmptyString,
    Sha256Hex,
    StrictContractModel,
    Timestamp,
)
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.task import DetailedAnalysisTaskV1
from stock_research_llm_orchestrator.preparation.market_evidence import (
    MarketPreparationIndex,
    validate_market_preparation,
)
from stock_research_llm_orchestrator.preparation.storage import publish_preparation
from stock_research_llm_orchestrator.sources.yfinance.normalization import (
    NormalizedPrices,
    PriceQualityIssue,
    TradingDates,
)


class ResearchCalendarMetadata(StrictContractModel):
    """Bind a supplied research calendar to its documented derivation, not source approval."""

    evidence_id: Identifier
    checked_at: Timestamp
    calendar_sha256: Sha256Hex
    research_note_sha256: Sha256Hex
    source_references: tuple[NonEmptyString, ...] = Field(min_length=1)
    rules: tuple[NonEmptyString, ...] = Field(min_length=1)
    scope: Literal["research_only"] = "research_only"


class PublicationObservation(StrictContractModel):
    """A manually recorded page observation with truthful day-level precision."""

    evidence_id: Identifier
    source_reference: Literal["https://www.jpx.co.jp/markets/statistics-equities/misc/01.html"]
    observed_on: str
    recorded_at: Timestamp
    latest_published_month: str = Field(pattern=r"^[0-9]{4}-(?:0[1-9]|1[0-2])$")
    research_note_sha256: Sha256Hex
    method: Literal["manual_research_note_without_retained_html"] = "manual_research_note_without_retained_html"

    @field_validator("observed_on")
    @classmethod
    def valid_day(cls, value: str) -> str:
        """Require a real ISO calendar date."""
        return _valid_day(value)


class RevalidationFindings(StrictContractModel):
    """Separate limited research findings from original quality and runtime acceptance."""

    task_id: Identifier
    evaluation_policy_version: int = Field(ge=1)
    original_index_sha256: Sha256Hex
    expected_count: int = Field(ge=0)
    observed_count: int = Field(ge=0)
    missing_dates: tuple[str, ...]
    unexpected_dates: tuple[str, ...]
    scheduled_dates_match: bool
    latest_published_month_match: Literal["matched", "mismatched", "unconfirmed"]
    publication_observed_on: str | None
    original_issues: tuple[PriceQualityIssue, ...]
    original_price_quality_passed: bool
    current_listing_eligibility: Literal["unconfirmed"] = "unconfirmed"


class MarketRevalidationIndex(StrictContractModel):
    """Versioned internal receipt, never a frozen evidence set or overall acceptance."""

    version: Literal[1] = 1
    kind: Literal["internal-market-evidence-revalidation"] = "internal-market-evidence-revalidation"
    revalidation_policy: Literal["research-market-revalidation-v1"] = "research-market-revalidation-v1"
    checked_at: Timestamp
    status: Literal["revalidated_with_gaps"] = "revalidated_with_gaps"
    analysis_ready: Literal[False] = False
    source_approval: Literal["research_only"] = "research_only"
    findings: RevalidationFindings
    files: tuple[FileReferenceV1, ...]
    limitations: tuple[str, ...] = (
        "Calendar provenance is supplied research documentation, not runtime source approval.",
        "Scheduled dates do not certify emergency closures or individual trading halts.",
        "Monthly matching applies only to the manual observation day, not revalidation time or workbook revision.",
        "Price values are not recalculated or independently verified; original quality findings are retained.",
        "Current listing eligibility and full analysis readiness remain unconfirmed.",
    )


def _valid_day(value: str) -> str:
    if date.fromisoformat(value).isoformat() != value:
        raise ValueError("invalid_iso_day")
    return value


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() is None:
        raise ValueError("timezone_required")
    return parsed


def _findings(files: Mapping[str, bytes], checked_at: str) -> RevalidationFindings:
    now = _timestamp(checked_at)
    prepared = {
        name.removeprefix("preparation/"): body for name, body in files.items() if name.startswith("preparation/")
    }
    validate_market_preparation(prepared)
    original = MarketPreparationIndex.model_validate_json(prepared["index.json"])
    task = DetailedAnalysisTaskV1.model_validate_json(prepared["task.json"])
    normalized = NormalizedPrices.model_validate_json(prepared["normalized.json"])
    metadata = ResearchCalendarMetadata.model_validate_json(files["calendar-metadata.json"])
    if _timestamp(original.prepared_at) > now or _timestamp(metadata.checked_at) > now:
        raise ValueError("future_input_timestamp")
    note_hash = sha256(files["research-note.md"]).hexdigest()
    if (
        sha256(files["calendar.json"]).hexdigest() != metadata.calendar_sha256
        or note_hash != metadata.research_note_sha256
    ):
        raise ValueError("research_hash_mismatch")
    calendar = TradingDates.model_validate_json(files["calendar.json"])
    period = normalized.requested_period
    if calendar.period.start_date > period.start_date or calendar.period.end_date < period.end_date:
        raise ValueError("calendar_period_incomplete")
    days = tuple(_valid_day(row.on) for row in normalized.rows)
    if days != tuple(sorted(set(days))) or any(not period.start_date <= day <= period.end_date for day in days):
        raise ValueError("invalid_observed_dates")
    expected = {day for day in calendar.dates if period.start_date <= day <= period.end_date}
    observed = set(days)
    monthly: Literal["matched", "mismatched", "unconfirmed"] = "unconfirmed"
    observed_on = None
    if "publication-observation.json" in files:
        observation = PublicationObservation.model_validate_json(files["publication-observation.json"])
        recorded_at = _timestamp(observation.recorded_at)
        if (
            recorded_at > now
            or observation.observed_on > recorded_at.astimezone(ZoneInfo("Asia/Tokyo")).date().isoformat()
            or observation.latest_published_month > observation.observed_on[:7]
        ):
            raise ValueError("future_publication_observation")
        if observation.research_note_sha256 != note_hash:
            raise ValueError("observation_note_hash_mismatch")
        observed_on = observation.observed_on
        monthly = (
            "matched"
            if original.jpx_verification.snapshot_on[:7] == observation.latest_published_month
            else "mismatched"
        )
    return RevalidationFindings(
        task_id=task.task_id,
        evaluation_policy_version=task.evaluation_policy_version,
        original_index_sha256=sha256(prepared["index.json"]).hexdigest(),
        expected_count=len(expected),
        observed_count=len(observed),
        missing_dates=tuple(sorted(expected - observed)),
        unexpected_dates=tuple(sorted(observed - expected)),
        scheduled_dates_match=expected == observed,
        latest_published_month_match=monthly,
        publication_observed_on=observed_on,
        original_issues=original.issues,
        original_price_quality_passed=original.price_quality_passed,
    )


def validate_market_revalidation(files: Mapping[str, bytes]) -> None:
    """Verify every retained byte and reproduce the findings from retained inputs."""
    index = MarketRevalidationIndex.model_validate_json(files["index.json"])
    paths = tuple(reference.relative_path for reference in index.files)
    ids = tuple(reference.artifact_id for reference in index.files)
    if len(set(paths)) != len(paths) or len(set(ids)) != len(ids) or set(paths) != set(files) - {"index.json"}:
        raise ValueError("invalid_revalidation_reference_set")
    support = {name for name in paths if not name.startswith("preparation/")}
    required = {"calendar.json", "calendar-metadata.json", "research-note.md"}
    if support not in (required, required | {"publication-observation.json"}):
        raise ValueError("invalid_revalidation_support_set")
    for reference in index.files:
        if sha256(files[reference.relative_path]).hexdigest() != reference.sha256:
            raise ValueError("revalidation_hash_mismatch")
    if index.findings != _findings(files, index.checked_at):
        raise ValueError("revalidation_findings_mismatch")


def _safe_path(path: Path) -> Path:
    """Reject traversal and symlink components within a caller-controlled filesystem."""
    if ".." in path.parts:
        raise ValueError("path_traversal")
    absolute = path.absolute()
    if any(part.is_symlink() for part in (absolute, *absolute.parents)):
        raise ValueError("symlink_input_or_output")
    return absolute


def _read_file(path: Path) -> bytes:
    safe = _safe_path(path)
    if not safe.is_file():
        raise ValueError("regular_file_required")
    return safe.read_bytes()


def revalidate_market_evidence(
    *,
    preparation: Path,
    calendar: Path,
    calendar_metadata: Path,
    research_note: Path,
    output: Path,
    checked_at: datetime,
    publication_observation: Path | None = None,
) -> MarketRevalidationIndex:
    """Publish a self-contained research receipt without network access or input edits.

    The caller must exclusively control the input paths and existing output parent;
    concurrent filesystem mutation is outside the storage utility's guarantees.
    """
    destination = _safe_path(output)
    source = _safe_path(preparation)
    inputs = [source, _safe_path(calendar), _safe_path(calendar_metadata), _safe_path(research_note)]
    if publication_observation is not None:
        inputs.append(_safe_path(publication_observation))
    if any(destination == path or destination in path.parents or path in destination.parents for path in inputs):
        raise ValueError("overlapping_input_and_output")
    if destination.exists():
        raise FileExistsError("revalidation_destination_exists")
    if not source.is_dir():
        raise ValueError("preparation_directory_required")
    # Preparation v1 publishes flat files. Never follow source-controlled reference paths.
    files = {f"preparation/{path.name}": _read_file(path) for path in sorted(source.iterdir())}
    files.update(
        {
            "calendar.json": _read_file(calendar),
            "calendar-metadata.json": _read_file(calendar_metadata),
            "research-note.md": _read_file(research_note),
        }
    )
    if publication_observation is not None:
        files["publication-observation.json"] = _read_file(publication_observation)
    stamp = checked_at.isoformat()
    findings = _findings(files, stamp)
    references = tuple(
        FileReferenceV1(
            artifact_id=name.replace("/", ":"),
            relative_path=name,
            media_type="application/octet-stream",
            sha256=sha256(body).hexdigest(),
        )
        for name, body in sorted(files.items())
    )
    index = MarketRevalidationIndex(checked_at=stamp, findings=findings, files=references)
    files["index.json"] = index.model_dump_json(indent=2).encode() + b"\n"
    publish_preparation(destination.parent, destination.name, files, validator=validate_market_revalidation)
    return index
