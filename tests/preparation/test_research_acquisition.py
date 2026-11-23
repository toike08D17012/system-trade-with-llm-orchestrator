"""Standard acquisition preserves originals, classification, status and replay bindings."""

import io
import json
import socket
import zipfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pandas as pd  # type: ignore[import-untyped]
import pytest

from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.acquisition_analysis import AcquisitionRequest
from stock_research_llm_orchestrator.preparation.market_evidence import JPX_MEDIA_TYPE
from stock_research_llm_orchestrator.preparation.research_acquisition import (
    ResearchAcquisitionExecutor,
    validate_acquisition,
)
from stock_research_llm_orchestrator.preparation.task_input import create_human_selected_task
from stock_research_llm_orchestrator.sources.yfinance.adapter import YfinanceDailyAdapter
from stock_research_llm_orchestrator.sources.yfinance.native_history import NativeHistoryClient
from stock_research_llm_orchestrator.sources.yfinance.normalization import COLUMNS


NOW = datetime(2026, 11, 14, 3, tzinfo=UTC)


def _executor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, respond: httpx.MockTransport
) -> ResearchAcquisitionExecutor:
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *_a, **_k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", 443))]
    )
    task = create_human_selected_task(
        "7203", 1, mic="XTKS", task_id_factory=lambda: "acquisition-test", accepted_at_factory=lambda: NOW
    )
    return ResearchAcquisitionExecutor(
        task=task,
        runtime=tmp_path / "runtime",
        runs=tmp_path / "runs",
        evaluated_at=NOW,
        clock=lambda: NOW,
        transport=respond,
    )


def _request(classification: str = "public-web", purpose: str = "Retrieve missing evidence") -> AcquisitionRequest:
    return AcquisitionRequest.model_validate(
        {
            "operation": "public_document",
            "security_code": "7203",
            "purpose": purpose,
            "url": "https://example.com/results",
            "classification": classification,
        }
    )


@pytest.mark.parametrize("classification", ["public-web", "issuer-ir", "jpx"])
def test_document_fetch_and_offline_reconstruction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, classification: str
) -> None:
    """Documents retain their actual publisher class instead of all becoming issuer IR."""
    seen: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(
            200, content=b"<h1>Current result</h1><p>Dividend 100</p>", headers={"Content-Type": "text/html"}
        )

    executor = _executor(tmp_path, monkeypatch, httpx.MockTransport(respond))
    request = _request(classification)
    result, files = executor(request, "track_a_income", 1)
    assert result.status == "acquired", result.detail
    assert result.evidence[0].source_id == classification
    assert result.evidence[0].text == "Current result\nDividend 100"
    validate_acquisition(request, result, files)
    assert seen == [request.url]
    modified = result.model_copy(
        update={"evidence": (result.evidence[0].model_copy(update={"text": "Dividend 10000"}),)}
    )
    with pytest.raises(ValueError, match="document_reconstruction"):
        validate_acquisition(request, modified, files)


def test_same_target_reuses_raw_without_claiming_independent_acquisition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Different role purposes do not cause repeated identical transport sends."""
    sends: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        sends.append(str(request.url))
        return httpx.Response(200, content=b"Result", headers={"Content-Type": "text/plain"})

    executor = _executor(tmp_path, monkeypatch, httpx.MockTransport(respond))
    first, _ = executor(_request(), "track_a_capital", 1)
    request = _request(purpose="Independently interpret dividend coverage")
    second, files = executor(request, "track_b_income", 2)
    assert second.status == first.status == "acquired"
    assert second.reuse_reference is not None
    assert not second.attempt_references
    validate_acquisition(request, second, files)
    assert len(sends) == 1


def test_retained_acquisition_seeds_new_executor_without_network_or_new_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Resumption reuses validated public originals and preserves their physical provenance."""
    first_executor = _executor(
        tmp_path / "first",
        monkeypatch,
        httpx.MockTransport(
            lambda _r: httpx.Response(
                200, content=b"Retained dividend disclosure", headers={"Content-Type": "text/plain"}
            )
        ),
    )
    original_request = _request()
    original, original_files = first_executor(original_request, "track_a_income", 1)
    assert original.status == "acquired", original.detail
    reused_request = _request(purpose="Check retained dividend coverage.")
    retained, retained_files = first_executor(reused_request, "track_b_income", 1)

    def forbidden_send(_request: httpx.Request) -> httpx.Response:
        pytest.fail("Retained source reuse must not send another network request.")

    resumed = ResearchAcquisitionExecutor(
        task=first_executor.task,
        runtime=tmp_path / "resumed-runtime",
        runs=tmp_path / "resumed-runs",
        evaluated_at=NOW + timedelta(minutes=1),
        clock=lambda: NOW + timedelta(minutes=1),
        transport=httpx.MockTransport(forbidden_send),
        retained_acquisitions=((reused_request, retained, retained_files),),
    )
    result, files = resumed(reused_request, "primary_reviewer", 2)
    assert result.status == "acquired"
    assert result.evidence == original.evidence
    assert not result.attempt_references
    assert result.reuse_reference is not None
    assert result.evidence[0].retrieved_at == original.evidence[0].retrieved_at
    validate_acquisition(reused_request, result, files)
    validate_acquisition(original_request, original, original_files)


def test_retained_acquisition_rejects_other_security_before_source_reuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A saved raw receipt cannot silently change the requested security identity."""
    executor = _executor(
        tmp_path,
        monkeypatch,
        httpx.MockTransport(lambda _r: httpx.Response(200, content=b"Result", headers={"Content-Type": "text/plain"})),
    )
    request = _request()
    result, files = executor(request, "track_a_capital", 1)
    other_request = request.model_copy(update={"security_code": "1234"})
    with pytest.raises(ValueError, match="acquisition_retained_security_mismatch"):
        ResearchAcquisitionExecutor(
            task=executor.task,
            runtime=tmp_path / "resumed-runtime",
            runs=tmp_path / "resumed-runs",
            evaluated_at=NOW,
            retained_acquisitions=((other_request, result, files),),
        )


def test_http_error_has_actual_attempt_and_missing_input_does_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real HTTP failure differs from rejected input that was never sent."""
    executor = _executor(
        tmp_path,
        monkeypatch,
        httpx.MockTransport(lambda _r: httpx.Response(503, content=b"down", headers={"Content-Type": "text/plain"})),
    )
    request = _request()
    result, files = executor(request, "orchestrator", 1)
    assert result.status == "failed"
    assert result.attempt_references
    assert "transport_started" in json.dumps(json.loads(files[result.attempt_references[0].relative_path]))
    invalid = request.model_copy(update={"url": "http://example.com/results"})
    rejected, payload = executor(invalid, "orchestrator", 2)
    assert rejected.status == "not_attempted"
    assert not rejected.attempt_references
    validate_acquisition(invalid, rejected, payload)


def test_source_gate_prevents_rapid_different_document_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """All roles and classifications share the same communication throttle."""
    executor = _executor(
        tmp_path,
        monkeypatch,
        httpx.MockTransport(lambda _r: httpx.Response(200, content=b"Result", headers={"Content-Type": "text/plain"})),
    )
    executor(_request(), "track_a_income", 1)
    next_request = _request("issuer-ir").model_copy(update={"url": "https://example.com/other"})
    result, _ = executor(next_request, "track_b_income", 1)
    assert result.status in {"waiting", "not_attempted"}
    assert not result.evidence
    executor.clock = lambda: NOW + timedelta(seconds=61)
    resumed, _ = executor(next_request, "track_b_income", 2)
    assert resumed.status == "acquired", resumed.detail


def _workbook() -> bytes:
    values = (
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
        "20261030",
        "7203",
        "Synthetic issuer",
        "プライム（内国株式）",
        "",
        "",
        "",
        "",
        "",
        "",
    )
    shared = (
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        + "".join(f"<si><t>{v}</t></si>" for v in values)
        + "</sst>"
    )
    rows = []
    for number in (1, 2):
        cells = "".join(f'<c r="{chr(65 + c)}{number}" t="s"><v>{(number - 1) * 10 + c}</v></c>' for c in range(10))
        rows.append(f'<row r="{number}">{cells}</row>')
    sheet = (
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
        + "".join(rows)
        + "</sheetData></worksheet>"
    )
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("xl/sharedStrings.xml", shared)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    return output.getvalue()


def test_listing_then_market_uses_verified_identity_and_retains_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The market route uses a real native adapter and keeps full raw series with quality gaps."""
    body = _workbook()
    executor = _executor(
        tmp_path,
        monkeypatch,
        httpx.MockTransport(lambda _r: httpx.Response(200, content=body, headers={"Content-Type": JPX_MEDIA_TYPE})),
    )
    frame = pd.DataFrame(
        {column: [100, 105] if column not in {"Volume", "Dividends", "Stock Splits"} else [0, 0] for column in COLUMNS},
        index=pd.date_range("2026-11-12", periods=2, tz="Asia/Tokyo"),
    )

    class Ticker:
        def __init__(self) -> None:
            self._price_history = SimpleNamespace(
                _history_metadata={
                    "currency": "JPY",
                    "exchangeTimezoneName": "Asia/Tokyo",
                    "symbol": "7203.T",
                    "instrumentType": "EQUITY",
                }
            )

        def history(self, **_kwargs: object) -> object:
            return frame

    executor.adapter = YfinanceDailyAdapter(
        history_client=NativeHistoryClient(
            tmp_path / "native", ticker_factory=lambda _s: Ticker(), clock=lambda: NOW.timestamp()
        )
    )
    listing_request = AcquisitionRequest(operation="listing", security_code="7203", purpose="Resolve identity")
    listing, _ = executor(listing_request, "orchestrator", 1)
    assert listing.status == "acquired", listing.detail
    request = AcquisitionRequest(
        operation="market",
        security_code="7203",
        purpose="Obtain prices and dividend history",
        start=date(2026, 11, 12),
        end=date(2026, 11, 14),
    )
    result, files = executor(request, "track_b_capital", 2)
    assert result.status == "acquired", result.detail
    assert result.validation == "pending"
    assert {e.source_id for e in result.evidence} == {"jpx", "yfinance"}
    price = next(e for e in result.evidence if e.source_id == "yfinance")
    assert json.loads(price.text)["latest_observation"]["close"] == "105"
    assert not json.loads(price.text)["quality_passed"]
    assert not json.loads(price.text)["latest_price_verified"]
    assert price.source_reference == "yfinance:Ticker.history:7203.T"
    validate_acquisition(request, result, files)
    modified = result.model_copy(
        update={
            "evidence": tuple(
                e.model_copy(
                    update={
                        "text": e.text.replace(
                            '"provider_identity_verified": true', '"provider_identity_verified": false'
                        )
                    }
                )
                if e.source_id == "yfinance"
                else e
                for e in result.evidence
            )
        }
    )
    with pytest.raises(ValueError, match="market_reconstruction"):
        validate_acquisition(request, modified, files)


def test_identical_returned_prices_keep_distinct_period_observation_identities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Equal provider CSV bytes do not alias distinct periods or retrieval observations."""
    executor = _executor(
        tmp_path,
        monkeypatch,
        httpx.MockTransport(
            lambda _r: httpx.Response(200, content=_workbook(), headers={"Content-Type": JPX_MEDIA_TYPE})
        ),
    )
    current = [NOW]
    executor.clock = lambda: current[0]
    frame = pd.DataFrame(
        {column: [100, 105] if column not in {"Volume", "Dividends", "Stock Splits"} else [0, 0] for column in COLUMNS},
        index=pd.date_range("2026-11-12", periods=2, tz="Asia/Tokyo"),
    )
    calls: list[dict[str, object]] = []

    class Ticker:
        def __init__(self) -> None:
            self._price_history = SimpleNamespace(
                _history_metadata={
                    "currency": "JPY",
                    "exchangeTimezoneName": "Asia/Tokyo",
                    "symbol": "7203.T",
                    "instrumentType": "EQUITY",
                }
            )

        def history(self, **kwargs: object) -> object:
            calls.append(kwargs)
            return frame

    executor.adapter = YfinanceDailyAdapter(
        history_client=NativeHistoryClient(
            tmp_path / "native", ticker_factory=lambda _s: Ticker(), clock=lambda: current[0].timestamp()
        )
    )
    first_request = AcquisitionRequest(
        operation="market",
        security_code="7203",
        purpose="Obtain dividend observations around the first date.",
        start=date(2026, 11, 11),
        end=date(2026, 11, 14),
    )
    first, first_files = executor(first_request, "track_a_income", 1)
    assert first.status == "acquired", first.detail
    current[0] += timedelta(seconds=11)
    second_request = first_request.model_copy(update={"start": date(2026, 11, 12)})
    second, second_files = executor(second_request, "track_b_income", 1)
    assert second.status == "acquired", second.detail
    first_price = next(item for item in first.evidence if item.source_id == "yfinance")
    second_price = next(item for item in second.evidence if item.source_id == "yfinance")
    assert first_price.raw_reference.sha256 == second_price.raw_reference.sha256
    assert first_price.evidence_id != second_price.evidence_id
    assert first_price.retrieved_at != second_price.retrieved_at
    assert (
        json.loads(first_price.text)["normalized_prices"]["requested_period"]
        != json.loads(second_price.text)["normalized_prices"]["requested_period"]
    )
    validate_acquisition(first_request, first, first_files)
    validate_acquisition(second_request, second, second_files)

    repeated_request = first_request.model_copy(update={"purpose": "Independently interpret the retained original."})
    repeated, repeated_files = executor(repeated_request, "primary_reviewer", 2)
    repeated_price = next(item for item in repeated.evidence if item.source_id == "yfinance")
    assert repeated_price.evidence_id == first_price.evidence_id
    assert repeated.reuse_reference is not None
    assert len(calls) == 2
    validate_acquisition(repeated_request, repeated, repeated_files)


def test_waiting_for_known_deadline_preserves_deferred_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Live callers can honor source intervals without asking models to busy retry."""
    executor = _executor(
        tmp_path,
        monkeypatch,
        httpx.MockTransport(lambda _r: httpx.Response(200, content=b"Result", headers={"Content-Type": "text/plain"})),
    )
    executor(_request(), "baseline", 0)
    current = NOW
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        nonlocal current
        sleeps.append(seconds)
        current += timedelta(seconds=seconds)

    executor.wait_for_gate = True
    executor.clock = lambda: current
    executor.sleeper = sleep
    request = _request().model_copy(update={"url": "https://example.com/second"})
    result, files = executor(request, "track_a_capital", 1)
    assert result.status == "acquired", result.detail
    assert sleeps == [60.0]
    assert any(name.startswith("gate-waits/0/") and name.endswith("result.json") for name in files)
    validate_acquisition(request, result, files)
