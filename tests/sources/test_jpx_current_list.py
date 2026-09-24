"""Tests for the approved JPX current-list adapter and transport."""

import hashlib
import io
import zipfile

import httpx
import pytest

from stock_research_llm_orchestrator.sources.jpx.current_list import (
    JpxCurrentListAdapter,
    JpxCurrentListParseError,
)
from stock_research_llm_orchestrator.sources.jpx.httpx_transport import (
    JpxHttpClientPolicy,
    JpxHttpTransportError,
    JpxPhysicalTransport,
)
from stock_research_llm_orchestrator.sources.protocol import BoundedSourceResponse


def _workbook(*rows: tuple[str, ...]) -> bytes:
    strings = (
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
    ) + tuple(value for row in rows for value in row)
    shared = (
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        + "".join(f"<si><t>{value}</t></si>" for value in strings)
        + "</sst>"
    )
    data_rows = []
    offset = 0
    for number, _row in enumerate((strings[:10], *rows), start=1):
        if number == 1:
            indexes = range(10)
        else:
            indexes = range(10 + offset, 20 + offset)
            offset += 10
        cells = "".join(
            f'<c r="{chr(65 + column)}{number}" t="s"><v>{index}</v></c>' for column, index in enumerate(indexes)
        )
        data_rows.append(f'<row r="{number}">{cells}</row>')
    sheet = (
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
        + "".join(data_rows)
        + "</sheetData></worksheet>"
    )
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("xl/sharedStrings.xml", shared)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    return output.getvalue()


def _body() -> bytes:
    return _workbook(
        ("20260831", "1301", "極洋", "プライム（内国株式）", "0050", "水産・農林業", "1", "食品", "6", "TOPIX Small 1"),
        ("20260831", "1305", "ＥＴＦ", "ETF・ETN", "", "", "", "", "", ""),
    )


def test_parses_eligible_domestic_common_equity_without_guessing_other_classes() -> None:
    """Classify only the three explicitly approved domestic-equity categories."""
    body = _body()
    parsed = JpxCurrentListAdapter().parse(
        BoundedSourceResponse(
            physical_attempt_id="attempt-1",
            body=body,
            sha256=hashlib.sha256(body).hexdigest(),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            encoding="binary",
        )
    )
    assert parsed.snapshot_on == "2026-08-31"
    assert parsed.issues[0].eligibility == "eligible"
    assert parsed.issues[0].market_segment == "Prime"
    assert parsed.issues[1].eligibility == "ineligible"
    assert parsed.issues[1].security_class == "unknown"


@pytest.mark.parametrize("body", [b"not-xlsx", _workbook(("20260831",) * 10)])
def test_rejects_malformed_or_unrecognized_workbook(body: bytes) -> None:
    """Fail closed without exposing workbook content."""
    with pytest.raises(JpxCurrentListParseError, match="jpx_current_list_invalid"):
        JpxCurrentListAdapter().parse(
            BoundedSourceResponse(
                physical_attempt_id="attempt-1",
                body=body,
                sha256=hashlib.sha256(body).hexdigest(),
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                encoding="binary",
            )
        )


def test_transport_uses_fixed_url_and_rejects_redirect() -> None:
    """Perform one anonymous fixed-resource request with no redirect following."""
    intent = JpxCurrentListAdapter().build_intent("current-listed-issues", ())
    body = _body()
    seen: list[httpx.Request] = []

    def ok(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            headers={"Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
            content=body,
        )

    callback = JpxPhysicalTransport(
        intent,
        JpxHttpClientPolicy(max_response_bytes=len(body), connect_timeout_seconds=1, read_timeout_seconds=2),
        httpx.MockTransport(ok),
    )
    response = callback(intent.to_transport_request("logical-1", "attempt-1"))
    assert response.body == body
    assert seen[0].url == "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xlsx"

    redirect = JpxPhysicalTransport(
        intent,
        JpxHttpClientPolicy(max_response_bytes=len(body), connect_timeout_seconds=1, read_timeout_seconds=2),
        httpx.MockTransport(lambda _request: httpx.Response(302, headers={"Location": "https://example.invalid"})),
    )
    with pytest.raises(JpxHttpTransportError, match="jpx_http_redirect_rejected"):
        redirect(intent.to_transport_request("logical-2", "attempt-2"))
