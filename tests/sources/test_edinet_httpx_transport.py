"""Offline compatibility tests for the pinned EDINET HTTPX wire client."""

import gzip
import logging

import httpx
import pytest

from stock_research_llm_orchestrator.sources import (
    EdinetHttpClientError,
    EdinetHttpClientPolicy,
    EdinetHttpTarget,
    HttpxEdinetWireClient,
    SourceParameter,
)


CANARY = "dummy-edinet-httpx-key-do-not-log"
POLICY = EdinetHttpClientPolicy(
    max_response_bytes=32,
    connect_timeout_seconds=1,
    read_timeout_seconds=2,
    write_timeout_seconds=3,
    pool_timeout_seconds=4,
)
TARGET = EdinetHttpTarget(
    origin="api.edinet-fsa.go.jp",
    path="/api/v2/documents.json",
    query=(SourceParameter(name="date", value="2026-09-24"), SourceParameter(name="type", value="2")),
)


def test_sends_query_key_with_explicit_policy_and_no_dependency_log(caplog: pytest.LogCaptureFixture) -> None:
    """Apply the approved transient URL exception without emitting dependency logs."""
    observed: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request)
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json; charset=utf-8", "Content-Encoding": "gzip"},
            content=gzip.compress(b'{"status":"ok"}'),
        )

    for name in ("httpx", "httpcore"):
        logging.getLogger(name).disabled = False
    caplog.set_level(logging.DEBUG)
    response = HttpxEdinetWireClient(POLICY, httpx.MockTransport(handler))(TARGET, CANARY)

    assert response.body == b'{"status":"ok"}'
    assert response.media_type == "application/json"
    assert response.encoding == "utf-8"
    assert observed[0].url.params["Subscription-Key"] == CANARY
    assert observed[0].extensions["timeout"] == {"connect": 1.0, "read": 2.0, "write": 3.0, "pool": 4.0}
    assert CANARY not in caplog.text
    assert logging.getLogger("httpx").disabled
    assert logging.getLogger("httpcore").disabled


def test_rejects_decompressed_body_over_limit() -> None:
    """Enforce the byte limit after HTTPX content decoding."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json", "Content-Encoding": "gzip"},
            content=gzip.compress(b"x" * 33),
        )

    with pytest.raises(EdinetHttpClientError, match="^edinet_http_response_too_large$"):
        HttpxEdinetWireClient(POLICY, httpx.MockTransport(handler))(TARGET, CANARY)


def test_rejects_redirect_without_following_it() -> None:
    """Reject the first redirect response without issuing a second request."""
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(302, headers={"Location": "https://example.invalid/secret"})

    with pytest.raises(EdinetHttpClientError, match="^edinet_http_redirect_rejected$"):
        HttpxEdinetWireClient(POLICY, httpx.MockTransport(handler))(TARGET, CANARY)
    assert calls == 1


def test_sanitizes_httpx_exception() -> None:
    """Do not propagate a request-bearing HTTPX exception beyond the wire boundary."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout(CANARY, request=request)

    with pytest.raises(EdinetHttpClientError, match="^edinet_http_exchange_failed$") as captured:
        HttpxEdinetWireClient(POLICY, httpx.MockTransport(handler))(TARGET, CANARY)
    assert CANARY not in str(captured.value)
    assert CANARY not in repr(captured.value)
