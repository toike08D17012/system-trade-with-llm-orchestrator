"""Offline tests for the bounded anonymous BOJ HTTPX transport."""

import gzip
from pathlib import Path

import httpx
import pytest

from stock_research_llm_orchestrator.sources import (
    BojFxCodeAdapter,
    BojHttpClientPolicy,
    BojHttpTransportError,
    BojPhysicalTransport,
    SourceParameter,
)


FIXTURE = Path(__file__).parents[1] / "fixtures/sources/boj/fxerd04.json"
POLICY = BojHttpClientPolicy(
    max_response_bytes=16 * 1024,
    connect_timeout_seconds=1,
    read_timeout_seconds=2,
    write_timeout_seconds=3,
    pool_timeout_seconds=4,
)


def _intent():  # type: ignore[no-untyped-def]
    return BojFxCodeAdapter().build_intent(
        "fx-daily-code",
        (
            SourceParameter(name="code", value="FXERD04"),
            SourceParameter(name="db", value="FM08"),
            SourceParameter(name="end_date", value="202609"),
            SourceParameter(name="format", value="json"),
            SourceParameter(name="lang", value="en"),
            SourceParameter(name="start_date", value="202609"),
        ),
    )


def test_sends_approved_anonymous_query_and_decodes_gzip() -> None:
    """Map canonical parameters and enforce explicit HTTP policy without credentials."""
    body = FIXTURE.read_bytes()
    observed: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json; charset=utf-8", "Content-Encoding": "gzip"},
            content=gzip.compress(body),
        )

    intent = _intent()
    response = BojPhysicalTransport(intent, POLICY, httpx.MockTransport(handler))(
        intent.to_transport_request("logical-boj-1", "attempt-boj-1")
    )

    assert response.body == body
    assert response.media_type == "application/json"
    assert observed[0].url.path == "/api/v1/getDataCode"
    assert dict(observed[0].url.params) == {
        "code": "FXERD04",
        "db": "FM08",
        "endDate": "202609",
        "format": "json",
        "lang": "en",
        "startDate": "202609",
    }
    assert observed[0].headers["Accept-Encoding"] == "gzip"
    assert observed[0].extensions["timeout"] == {"connect": 1.0, "read": 2.0, "write": 3.0, "pool": 4.0}


def test_rejects_mismatch_before_send() -> None:
    """Do not send when the controlled physical identity was changed."""
    intent = _intent()
    request = intent.to_transport_request("logical-boj-2", "attempt-boj-2").model_copy(update={"operation": "other"})

    with pytest.raises(BojHttpTransportError, match="^boj_http_request_mismatch$"):
        BojPhysicalTransport(intent, POLICY, httpx.MockTransport(_unexpected_send))(request)


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (httpx.Response(302, headers={"Location": "https://example.invalid"}), "boj_http_redirect_rejected"),
        (
            httpx.Response(200, headers={"Content-Type": "application/json"}, content=b"x" * (16 * 1024 + 1)),
            "boj_http_response_too_large",
        ),
    ],
)
def test_rejects_redirect_and_decompressed_oversize(response: httpx.Response, reason: str) -> None:
    """Stop without redirect following or retaining an oversized body."""
    intent = _intent()
    with pytest.raises(BojHttpTransportError, match=f"^{reason}$"):
        BojPhysicalTransport(intent, POLICY, httpx.MockTransport(lambda _request: response))(
            intent.to_transport_request("logical-boj-3", "attempt-boj-3")
        )


def test_boj_transport_does_not_import_edinet_credentials() -> None:
    """Keep the anonymous source independent from the EDINET credential loader."""
    module = Path(__file__).parents[2] / "src/stock_research_llm_orchestrator/sources/boj/httpx_transport.py"
    text = module.read_text()
    assert "credentials.edinet" not in text
    assert "use_edinet_api_key_for_send" not in text


def _unexpected_send(_request: httpx.Request) -> httpx.Response:
    raise AssertionError("HTTP send must not run")
