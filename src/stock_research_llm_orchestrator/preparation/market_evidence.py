"""Prepare a self-contained price evidence slice from verified JPX source bytes."""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from hashlib import sha256
from pathlib import Path
from typing import Final, Literal
from zoneinfo import ZoneInfo

from pydantic import JsonValue, field_validator

from stock_research_llm_orchestrator.contracts.base import (
    FileReferenceV1,
    Identifier,
    Sha256Hex,
    StrictContractModel,
    Timestamp,
)
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.evidence import NormalizedEvidenceV1
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.task import DetailedAnalysisTaskV1
from stock_research_llm_orchestrator.preparation.storage import PreparationReceipt, publish_preparation
from stock_research_llm_orchestrator.sources.jpx.current_list import JpxCurrentListAdapter
from stock_research_llm_orchestrator.sources.jpx.verification import JpxSecurityVerification, verify_jpx_security
from stock_research_llm_orchestrator.sources.protocol import BoundedSourceResponse
from stock_research_llm_orchestrator.sources.yfinance.adapter import YfinanceDailyAdapter
from stock_research_llm_orchestrator.sources.yfinance.mapping import map_jpx_verification_to_yfinance_daily
from stock_research_llm_orchestrator.sources.yfinance.native_history import HistoryResult
from stock_research_llm_orchestrator.sources.yfinance.normalization import (
    NormalizedPrices,
    PriceQualityIssue,
    TradingDates,
    normalize_history,
)


JPX_WORKBOOK_URL: Final = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xlsx"
JPX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class JpxSnapshotProvenance(StrictContractModel):
    """Acquisition receipt supplied with exact retained JPX workbook bytes."""

    version: Literal[1] = 1
    source_id: Literal["jpx"] = "jpx"
    reference: Literal["https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xlsx"] = (
        JPX_WORKBOOK_URL
    )
    source_approval_version: Literal[1] = 1
    physical_attempt_id: Identifier
    retrieved_at: Timestamp
    sha256: Sha256Hex

    @field_validator("retrieved_at")
    @classmethod
    def valid_timestamp(cls, value: str) -> str:
        """Reject impossible dates, not just incorrectly shaped strings."""
        datetime.fromisoformat(value)
        return value


class CalendarProvenance(StrictContractModel):
    """Provenance for caller-verified local trading-calendar JSON bytes."""

    source_reference: str
    retrieved_at: Timestamp
    sha256: Sha256Hex

    @field_validator("retrieved_at")
    @classmethod
    def valid_timestamp(cls, value: str) -> str:
        """Reject invalid calendar acquisition timestamps."""
        datetime.fromisoformat(value)
        return value


@dataclass(frozen=True)
class CalendarInput:
    """Local exchange-calendar evidence; no calendar download is performed."""

    body: bytes
    provenance: CalendarProvenance


class MarketPreparationIndex(StrictContractModel):
    """Internal artifact index, explicitly distinct from the execution manifest."""

    version: Literal[1] = 1
    kind: Literal["internal-market-evidence-preparation"] = "internal-market-evidence-preparation"
    task_id: Identifier
    prepared_at: Timestamp
    status: Literal["prepared_with_gaps"] = "prepared_with_gaps"
    analysis_ready: Literal[False] = False
    jpx_verification: JpxSecurityVerification
    jpx_freshness: Literal["unconfirmed"] = "unconfirmed"
    provider_identity_verified: bool
    price_quality_passed: bool
    issues: tuple[PriceQualityIssue, ...]
    limitations: tuple[str, ...] = (
        "JPX membership is verified only as of the retained snapshot, not as of preparation time.",
        "Price data is a yfinance-returned table, not Yahoo HTTP response bytes.",
        "Adjusted OHLC is not supplied; adjusted_close is the provider-returned Adj Close.",
        "Individual dividend currencies are not independently verified by history metadata.",
        "This slice does not freeze an evidence set or complete financial, FX, or disclosure evidence.",
    )
    files: tuple[FileReferenceV1, ...]
    normalized_evidence: NormalizedEvidenceV1


@dataclass(frozen=True)
class MarketPreparationResult:
    """Published local preparation and its separately observable quality outcome."""

    index: MarketPreparationIndex
    receipt: PreparationReceipt


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _reference(name: str, content: bytes) -> FileReferenceV1:
    return FileReferenceV1(
        artifact_id=name.replace("/", ":"),
        relative_path=name,
        media_type=JPX_MEDIA_TYPE
        if name.endswith(".xlsx")
        else "text/csv"
        if name.endswith(".csv")
        else "application/json",
        sha256=sha256(content).hexdigest(),
    )


def _acquisition_metadata(result: HistoryResult, symbol: str, start: date, end: date) -> dict[str, object]:
    """Keep only the native client's documented metadata and exact invocation."""
    metadata = result.metadata
    expected = {
        "source": "Yahoo Finance",
        "library": "yfinance",
        "library_version": "1.7.0",
        "function": "Ticker.history",
        "symbol": symbol,
        "policy": "yfinance-native-history-v1",
        "source_approval_version": 3,
        "rate_unit": "library_call",
        "http_body_retained": False,
        "market_metadata_origin": "cached-history-chart-meta",
        "dividend_currency_verification": "not_independently_verified",
        "arguments": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "interval": "1d",
            "auto_adjust": False,
            "actions": True,
            "repair": False,
            "keepna": True,
            "timeout": 10,
        },
    }
    if any(metadata.get(key) != value for key, value in expected.items()):
        raise ValueError("history_provenance_mismatch")
    timestamp = metadata.get("retrieved_at")
    if not isinstance(timestamp, str) or datetime.fromisoformat(timestamp).utcoffset() is None:
        raise ValueError("history_retrieval_time_missing")
    observed = {
        key: metadata.get(key) for key in ("currency", "exchange_timezone", "response_symbol", "instrument_type")
    }
    if any(value is not None and not isinstance(value, str) for value in observed.values()):
        raise ValueError("invalid_history_market_metadata")
    return {**expected, **observed, "retrieved_at": timestamp, "artifact_kind": "yfinance_returned_dataframe"}


def validate_market_preparation(files: Mapping[str, bytes]) -> None:
    """Resolve every indexed artifact and evidence input against exact staged bytes."""
    index = MarketPreparationIndex.model_validate_json(files["index.json"])
    paths = tuple(reference.relative_path for reference in index.files)
    ids = tuple(reference.artifact_id for reference in index.files)
    if len(set(paths)) != len(paths) or len(set(ids)) != len(ids) or set(paths) != set(files) - {"index.json"}:
        raise ValueError("invalid_preparation_reference_set")
    for reference in index.files:
        if sha256(files[reference.relative_path]).hexdigest() != reference.sha256:
            raise ValueError("preparation_artifact_hash_mismatch")
    evidence = index.normalized_evidence
    expected_inputs = {"jpx.xlsx", "jpx-metadata.json", "prices.csv", "history-metadata.json"}
    if "calendar.json" in files:
        expected_inputs |= {"calendar.json", "calendar-metadata.json"}
        calendar_provenance = CalendarProvenance.model_validate_json(files["calendar-metadata.json"])
        if sha256(files["calendar.json"]).hexdigest() != calendar_provenance.sha256:
            raise ValueError("calendar_hash_mismatch")
        TradingDates.model_validate_json(files["calendar.json"])
    if set(evidence.input_evidence_ids) != expected_inputs:
        raise ValueError("invalid_normalized_input_references")
    if not set(evidence.input_evidence_ids).issubset(ids):
        raise ValueError("unresolved_evidence_input")
    normalized = NormalizedPrices.model_validate_json(files["normalized.json"])
    if (
        sha256(files["normalized.json"]).hexdigest() != evidence.content_sha256
        or normalized.model_dump(mode="json") != evidence.value
        or (normalized.quality_passed and index.provider_identity_verified) != index.price_quality_passed
    ):
        raise ValueError("normalized_evidence_mismatch")
    task = DetailedAnalysisTaskV1.model_validate_json(files["task.json"])
    if task.task_id != index.task_id or task.security.security_code != index.jpx_verification.requested_code:
        raise ValueError("preparation_task_mismatch")
    provenance = JpxSnapshotProvenance.model_validate_json(files["jpx-metadata.json"])
    snapshot = JpxCurrentListAdapter().parse(
        BoundedSourceResponse(
            physical_attempt_id=provenance.physical_attempt_id,
            body=files["jpx.xlsx"],
            sha256=provenance.sha256,
            media_type=JPX_MEDIA_TYPE,
            encoding="binary",
        )
    )
    if verify_jpx_security(snapshot, task.security.security_code) != index.jpx_verification:
        raise ValueError("preparation_jpx_verification_mismatch")


def prepare_market_evidence(
    *,
    task: DetailedAnalysisTaskV1,
    jpx_body: bytes,
    jpx_provenance: JpxSnapshotProvenance,
    start: date,
    end: date,
    adapter: YfinanceDailyAdapter,
    root: Path,
    destination_name: str,
    prepared_at: datetime,
    calendar: CalendarInput | None = None,
) -> MarketPreparationResult:
    """Verify identity, acquire one native history, and atomically publish a price slice.

    The caller exclusively owns a trusted root and supplies a time used to check
    input provenance. The final preparation timestamp is the later of that time
    and actual history acquisition. Unknown freshness is never promoted to passed.
    """
    task = DetailedAnalysisTaskV1.model_validate(task.model_dump())
    jpx_provenance = JpxSnapshotProvenance.model_validate(jpx_provenance.model_dump())
    if prepared_at.utcoffset() is None:
        raise ValueError("preparation_clock_requires_timezone")
    if root.is_symlink() or not root.is_dir():
        raise ValueError("preparation_root_invalid")
    # Match the publisher's name constraints before any provider call.
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", destination_name) is None:
        raise ValueError("preparation_destination_invalid")
    if (root / destination_name).exists() or (root / destination_name).is_symlink():
        raise FileExistsError("preparation_destination_exists")
    retrieved = datetime.fromisoformat(jpx_provenance.retrieved_at)
    if retrieved > prepared_at:
        raise ValueError("jpx_retrieval_in_future")
    if end > prepared_at.astimezone(ZoneInfo("Asia/Tokyo")).date():
        raise ValueError("price_request_includes_unfinished_day")
    response = BoundedSourceResponse(
        physical_attempt_id=jpx_provenance.physical_attempt_id,
        body=jpx_body,
        sha256=jpx_provenance.sha256,
        media_type=JPX_MEDIA_TYPE,
        encoding="binary",
    )
    snapshot = JpxCurrentListAdapter().parse(response)
    if date.fromisoformat(snapshot.snapshot_on) > retrieved.astimezone(ZoneInfo("Asia/Tokyo")).date():
        raise ValueError("jpx_snapshot_in_future")
    verification = verify_jpx_security(snapshot, task.security.security_code)
    intent = map_jpx_verification_to_yfinance_daily(verification, start=start, end=end)
    trading_dates = None
    if calendar is not None:
        if sha256(calendar.body).hexdigest() != calendar.provenance.sha256:
            raise ValueError("calendar_hash_mismatch")
        if datetime.fromisoformat(calendar.provenance.retrieved_at) > prepared_at:
            raise ValueError("calendar_retrieval_in_future")
        trading_dates = TradingDates.model_validate_json(calendar.body)
    result = adapter.acquire_history(intent)
    metadata = _acquisition_metadata(result, intent.symbol, start, end)
    currency = metadata["currency"]
    normalized = normalize_history(
        result.frame,
        start=start,
        end=end,
        currency=currency if isinstance(currency, str) else None,
        trading_dates=trading_dates,
    )
    issues = list(normalized.issues)
    issues.append(PriceQualityIssue(code="jpx_snapshot_freshness_unconfirmed"))
    provider_verified = True
    for field, expected in (
        ("response_symbol", intent.symbol),
        ("exchange_timezone", "Asia/Tokyo"),
        ("instrument_type", "EQUITY"),
    ):
        if metadata[field] != expected:
            provider_verified = False
            issues.append(
                PriceQualityIssue(
                    code="provider_identity_unconfirmed" if metadata[field] is None else "provider_identity_conflict",
                    field=field,
                )
            )
    files = {
        "task.json": task.model_dump_json(indent=2).encode() + b"\n",
        "jpx.xlsx": jpx_body,
        "jpx-metadata.json": jpx_provenance.model_dump_json(indent=2).encode() + b"\n",
        "prices.csv": result.frame.to_csv(lineterminator="\n").encode("utf-8"),
        "history-metadata.json": _json_bytes(metadata),
        "normalized.json": normalized.model_dump_json(indent=2).encode() + b"\n",
    }
    if calendar is not None:
        files["calendar.json"] = calendar.body
        files["calendar-metadata.json"] = calendar.provenance.model_dump_json(indent=2).encode() + b"\n"
    value: JsonValue = json.loads(files["normalized.json"])
    inputs: tuple[str, ...] = ("jpx.xlsx", "jpx-metadata.json", "prices.csv", "history-metadata.json")
    if calendar is not None:
        inputs += ("calendar.json", "calendar-metadata.json")
    evidence = NormalizedEvidenceV1(
        evidence_id="normalized-prices",
        layer="normalized",
        input_evidence_ids=inputs,
        value=value,
        missing_reason=None,
        unit="prices: source currency; volume: shares; split: ratio; dividends: provider value",
        content_sha256=sha256(files["normalized.json"]).hexdigest(),
        data_version=sha256(files["prices.csv"]).hexdigest(),
        normalization_logic_version=1,
    )
    history_at = datetime.fromisoformat(str(metadata["retrieved_at"]))
    if history_at < prepared_at:
        raise ValueError("history_retrieval_precedes_preparation")
    index = MarketPreparationIndex(
        task_id=task.task_id,
        prepared_at=max(prepared_at, history_at).isoformat(),
        jpx_verification=verification,
        provider_identity_verified=provider_verified,
        price_quality_passed=normalized.quality_passed and provider_verified,
        issues=tuple(issues),
        files=tuple(_reference(name, body) for name, body in sorted(files.items())),
        normalized_evidence=evidence,
    )
    files["index.json"] = index.model_dump_json(indent=2).encode() + b"\n"
    receipt = publish_preparation(root, destination_name, files, validator=validate_market_preparation)
    return MarketPreparationResult(index=index, receipt=receipt)
