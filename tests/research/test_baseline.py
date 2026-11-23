"""Exercise neutral baseline acquisitions and independent disclosure discovery."""

import io
import json
import zipfile
from collections.abc import Mapping
from hashlib import sha256
from typing import Literal, cast

import pandas as pd  # type: ignore[import-untyped]
import pytest

from stock_research_llm_orchestrator.agents.configuration import load_agent_configuration
from stock_research_llm_orchestrator.agents.research_provider import WebTurnResult
from stock_research_llm_orchestrator.contracts.base import FileReferenceV1
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.acquisition_analysis import (
    AcquiredEvidence,
    AcquisitionRequest,
    AcquisitionResult,
)
from stock_research_llm_orchestrator.preparation.research_acquisition import _market_text, request_sha256
from stock_research_llm_orchestrator.research.baseline import _status, prepare_baseline, validate_baseline
from stock_research_llm_orchestrator.research.workflow import ResearchRequest
from stock_research_llm_orchestrator.sources.jpx.current_list import JpxCurrentListAdapter
from stock_research_llm_orchestrator.sources.jpx.verification import verify_jpx_security
from stock_research_llm_orchestrator.sources.protocol import BoundedSourceResponse
from stock_research_llm_orchestrator.sources.yfinance.normalization import COLUMNS, normalize_history

from .test_configured import PROFILE
from .test_workflow import STAMP, _inputs


TRACE = b'{"type":"item.started","item":{"id":"search-1","type":"web_search"}}\n'
DOCUMENT = AcquisitionRequest(
    operation="public_document",
    security_code="1234",
    purpose="Get current earnings disclosure.",
    url="https://example.com/ir/current.pdf",
    classification="issuer-ir",
)


def _discovery(requests: tuple[AcquisitionRequest, ...] = (DOCUMENT,)) -> WebTurnResult:
    return WebTurnResult(
        json.dumps(
            {
                "queries": ["1234 latest earnings disclosure"],
                "requests": [item.model_dump(mode="json") for item in requests],
                "unavailable_information": [],
            }
        ).encode(),
        TRACE,
    )


def _workbook() -> bytes:
    columns = (
        "日付",
        "コード",
        "銘柄名",
        "市場・商品区分",
        "33業種コード",
        "33業種区分",
        "17業種コード",
        "17業種区分",
        "規模コード",
        "規模区分",
    )
    row = ("20261001", "1234", "Synthetic issuer", "プライム（内国株式）", "", "", "", "", "", "")
    shared = (
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        + "".join(f"<si><t>{item}</t></si>" for item in (*columns, *row))
        + "</sst>"
    )
    rows = []
    for number in (1, 2):
        cells = "".join(
            f'<c r="{chr(65 + col)}{number}" t="s"><v>{(number - 1) * 10 + col}</v></c>' for col in range(10)
        )
        rows.append(f'<row r="{number}">{cells}</row>')
    sheet = (
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
        + "".join(rows)
        + "</sheetData></worksheet>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("xl/sharedStrings.xml", shared)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    return buffer.getvalue()


def _acquire(request: AcquisitionRequest, role: str, turn: int) -> tuple[AcquisitionResult, Mapping[str, bytes]]:
    assert role == "baseline"
    assert turn >= 0
    fingerprint = request_sha256(request)
    prefix = f"acquisition/{fingerprint}"
    payload: dict[str, bytes] = {}
    if request.operation == "listing":
        raw = _workbook()
        raw_name = "jpx.xlsx"
        parsed = JpxCurrentListAdapter().parse(
            BoundedSourceResponse(
                physical_attempt_id="fixture-attempt",
                body=raw,
                sha256=sha256(raw).hexdigest(),
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                encoding="binary",
            )
        )
        text = json.dumps(
            {
                "verification": verify_jpx_security(parsed, "1234").model_dump(mode="json"),
                "freshness": "unconfirmed",
                "limitation": "Current published snapshot does not establish listing status at other dates.",
            }
        )
    elif request.operation == "market":
        assert request.start is not None and request.end is not None
        frame = pd.DataFrame(
            {column: ["100"] for column in COLUMNS}, index=pd.DatetimeIndex(["2026-10-29"], tz="Asia/Tokyo")
        )
        frame["Dividends"] = "0"
        frame["Stock Splits"] = "0"
        normalized = normalize_history(frame, start=request.start, end=request.end, currency="JPY")
        metadata = {
            "source": "Yahoo Finance",
            "library": "yfinance",
            "library_version": "1.7.0",
            "function": "Ticker.history",
            "symbol": "1234.T",
            "policy": "yfinance-native-history-v1",
            "source_approval_version": 3,
            "rate_unit": "library_call",
            "http_body_retained": False,
            "market_metadata_origin": "cached-history-chart-meta",
            "dividend_currency_verification": "not_independently_verified",
            "arguments": {
                "start": request.start.isoformat(),
                "end": request.end.isoformat(),
                "interval": "1d",
                "auto_adjust": False,
                "actions": True,
                "repair": False,
                "keepna": True,
                "timeout": 10,
            },
            "currency": "JPY",
            "exchange_timezone": "Asia/Tokyo",
            "response_symbol": "1234.T",
            "instrument_type": "EQUITY",
            "retrieved_at": STAMP,
            "artifact_kind": "yfinance_returned_dataframe",
        }
        raw = frame.to_csv(lineterminator="\n").encode()
        raw_name = "prices.csv"
        text = json.dumps(_market_text(normalized, metadata, "1234.T"))
        payload[prefix + "/normalized.json"] = normalized.model_dump_json().encode()
        payload[prefix + "/history-metadata.json"] = json.dumps(metadata).encode()
        payload[prefix + "/jpx.xlsx"] = _workbook()
    else:
        raw = b"Public issuer original."
        raw_name = "document.bin"
        text = raw.decode()
        payload[prefix + "/document-metadata.json"] = json.dumps(
            {
                "media_type": "text/plain",
                "url": request.url,
                "classification": request.classification,
                "retrieved_at": STAMP,
            }
        ).encode()
    attempt = json.dumps({"operation": request.operation, "physical_attempt": 1}).encode()
    raw_ref = FileReferenceV1(
        artifact_id=f"raw-{turn}",
        relative_path=f"{prefix}/{raw_name}",
        media_type="application/octet-stream",
        sha256=sha256(raw).hexdigest(),
    )
    attempt_ref = FileReferenceV1(
        artifact_id=f"attempt-{turn}",
        relative_path=f"{prefix}/attempt.json",
        media_type="application/json",
        sha256=sha256(attempt).hexdigest(),
    )
    payload.update({raw_ref.relative_path: raw, attempt_ref.relative_path: attempt})
    references = [raw_ref, attempt_ref]
    for name, body in payload.items():
        if name not in {raw_ref.relative_path, attempt_ref.relative_path}:
            references.append(
                FileReferenceV1(
                    artifact_id=f"metadata-{turn}",
                    relative_path=name,
                    media_type="application/json",
                    sha256=sha256(body).hexdigest(),
                )
            )
    source_id: Literal["jpx", "yfinance", "issuer-ir"] = (
        "jpx" if request.operation == "listing" else "yfinance" if request.operation == "market" else "issuer-ir"
    )
    result = AcquisitionResult(
        request_sha256=fingerprint,
        status="acquired",
        validation="pending" if source_id == "yfinance" else "verified",
        requested_at=STAMP,
        completed_at=STAMP,
        attempt_references=(attempt_ref,),
        files=tuple(references),
        evidence=(
            AcquiredEvidence(
                evidence_id=f"baseline-{request.operation}",
                source_id=source_id,
                source_reference=request.url or f"native:{request.operation}:1234",
                retrieved_at=STAMP,
                raw_reference=raw_ref,
                text=text,
                validation="pending" if source_id == "yfinance" else "verified",
            ),
        ),
        detail="Synthetic acquired original for baseline boundary validation.",
    )
    if request.operation == "market":
        listing_request = AcquisitionRequest(operation="listing", security_code="1234", purpose="Verify issuer")
        listing, _ = _acquire(listing_request, role, turn)
        jpx_ref = next(item for item in references if item.relative_path.endswith("jpx.xlsx"))
        listing_evidence = listing.evidence[0].model_copy(update={"raw_reference": jpx_ref})
        result = result.model_copy(update={"evidence": (*result.evidence, listing_evidence)})
    return result, payload


def _rehash(files: dict[str, bytes]) -> None:
    manifest = json.loads(files["manifest.json"])
    manifest["files"] = {key: sha256(body).hexdigest() for key, body in sorted(files.items()) if key != "manifest.json"}
    files["manifest.json"] = json.dumps(manifest).encode()


def test_baseline_acquires_all_categories_and_preserves_prior_before_discovery() -> None:
    """Listing and market status reach discovery, alongside untouched supplied IR."""
    seen: list[ResearchRequest] = []
    inputs = _inputs()

    def invoke(request: ResearchRequest) -> WebTurnResult:
        seen.append(request)
        return _discovery()

    files = prepare_baseline(inputs, load_agent_configuration(PROFILE), _acquire, invoke, at=STAMP)
    results = validate_baseline(files)
    assert [result.evidence[0].source_id for result in results] == ["jpx", "yfinance", "issuer-ir"]
    assert len(seen) == 1
    assert seen[0].stage == "baseline_discovery"
    assert seen[0].data["supplied_evidence"] == json.loads(inputs["common-input.json"])
    assert seen[0].data["acquisition_statuses"] == [_status(result) for result in results[:2]]
    assert inputs == _inputs()
    assert all(item.status == "acquired" for item in results)


def test_baseline_deduplicates_identical_discovery_requests() -> None:
    """Repeated discovery proposals do not duplicate a public document acquisition."""
    files = prepare_baseline(
        _inputs(),
        load_agent_configuration(PROFILE),
        _acquire,
        lambda _request: _discovery((DOCUMENT, DOCUMENT)),
        at=STAMP,
    )
    assert len(validate_baseline(files)) == 3


def test_baseline_preserves_unattempted_market_status_and_continues() -> None:
    """Acquisition gaps reach discovery honestly without becoming a complete-data gate."""
    seen: list[ResearchRequest] = []

    def acquire(request: AcquisitionRequest, role: str, turn: int) -> tuple[AcquisitionResult, Mapping[str, bytes]]:
        if request.operation != "market":
            return _acquire(request, role, turn)
        return AcquisitionResult(
            request_sha256=request_sha256(request),
            status="not_attempted",
            validation="pending",
            requested_at=STAMP,
            completed_at=STAMP,
            detail="Market route was not attempted in this synthetic execution.",
        ), {}

    def invoke(request: ResearchRequest) -> WebTurnResult:
        seen.append(request)
        return _discovery()

    files = prepare_baseline(_inputs(), load_agent_configuration(PROFILE), acquire, invoke, at=STAMP)
    assert [result.status for result in validate_baseline(files)] == ["acquired", "not_attempted", "acquired"]
    statuses = cast(list[dict[str, object]], seen[0].data["acquisition_statuses"])
    assert statuses[1]["status"] == "not_attempted"


def test_baseline_rejects_original_hash_tamper_even_with_rehashed_manifest() -> None:
    """The original-file binding remains effective beneath the baseline manifest."""
    files = prepare_baseline(
        _inputs(), load_agent_configuration(PROFILE), _acquire, lambda _request: _discovery(), at=STAMP
    )
    original = next(key for key in files if key.endswith("document.bin"))
    files[original] = b"Different original bytes."
    _rehash(files)
    with pytest.raises(ValueError):
        validate_baseline(files)


def test_baseline_rejects_other_security_discovery_before_fetch() -> None:
    """A discovery proposal cannot silently switch the selected security."""
    wrong = DOCUMENT.model_copy(update={"security_code": "9999"})
    acquired: list[str] = []

    def acquire(request: AcquisitionRequest, role: str, turn: int) -> tuple[AcquisitionResult, Mapping[str, bytes]]:
        acquired.append(request.operation)
        return _acquire(request, role, turn)

    with pytest.raises(ValueError, match="baseline_discovery_request_invalid"):
        prepare_baseline(
            _inputs(), load_agent_configuration(PROFILE), acquire, lambda _request: _discovery((wrong,)), at=STAMP
        )
    assert acquired == ["listing", "market"]


def test_baseline_requires_native_web_discovery_evidence() -> None:
    """Discovery prose does not substitute for an observed Web tool invocation."""
    response = _discovery()
    with pytest.raises(ValueError, match="baseline_discovery_native_web_not_observed"):
        prepare_baseline(
            _inputs(),
            load_agent_configuration(PROFILE),
            _acquire,
            lambda _request: WebTurnResult(response.body, b""),
            at=STAMP,
        )
