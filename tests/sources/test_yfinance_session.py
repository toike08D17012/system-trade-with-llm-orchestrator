"""Synthetic, no-network checks for the pinned Yahoo send state machine."""

import logging
from collections.abc import Iterator

import pytest
from curl_cffi import requests

from stock_research_llm_orchestrator.sources.yfinance.coordinated_session import (
    CoordinatedSession,
    CoordinatedSessionError,
    EphemeralYahooRequest,
    YahooExchangeReceipt,
    YahooRequestIdentity,
)


CHART = "https://query2.finance.yahoo.com/v8/finance/chart/7203.T"
BASIC_CRUMB = "https://query1.finance.yahoo.com/v1/test/getcrumb"
CSRF_CRUMB = "https://query2.finance.yahoo.com/v1/test/getcrumb"


class _Response:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        self.text = "crumb"
        self.content = b"crumb"
        self.headers: dict[str, str] = {}
        self.cookies: dict[str, str] = {}

    def json(self) -> dict[str, object]:
        return {}


class _SyntheticExchange:
    """Offline fixture implementing the same per-send port shape as production."""

    def __init__(self, statuses: list[int | Exception]) -> None:
        self._statuses: Iterator[int | Exception] = iter(statuses)
        self.calls: list[tuple[YahooRequestIdentity, EphemeralYahooRequest]] = []

    def exchange(self, identity: YahooRequestIdentity, request: EphemeralYahooRequest) -> YahooExchangeReceipt:
        self.calls.append((identity, request))
        status = next(self._statuses)
        if isinstance(status, Exception):
            raise status
        return YahooExchangeReceipt(status, _Response(status), object())


def test_session_is_curl_cffi_session_and_forces_no_redirects() -> None:
    """Every session send enters the port with redirect following disabled."""
    exchange = _SyntheticExchange([200, 200, 200])
    session = CoordinatedSession(exchange=exchange, expected_symbol="7203.T")
    assert isinstance(session, requests.Session)
    assert session.retry.count == 0
    session.get("https://fc.yahoo.com", allow_redirects=True)
    session.get(BASIC_CRUMB, allow_redirects=True)
    session.get(CHART, params={"crumb": "canary"})
    assert [call[0].resource_class for call in exchange.calls] == ["cookie", "crumb", "chart"]
    assert all(call[1].kwargs["allow_redirects"] is False for call in exchange.calls)
    assert "canary" not in repr(exchange.calls)


def test_cookie_404_allows_crumb_and_chart() -> None:
    """The optional cookie endpoint's 404 is not a failed chart acquisition."""
    exchange = _SyntheticExchange([404, 200, 200])
    session = CoordinatedSession(exchange=exchange, expected_symbol="7203.T")
    assert session.get("https://fc.yahoo.com").status_code == 404
    assert not session.poisoned
    assert session.get(BASIC_CRUMB).status_code == 200
    assert session.get(CHART).status_code == 200
    assert session.complete


@pytest.mark.parametrize(
    "url,status", [("https://fc.yahoo.com", 429), (BASIC_CRUMB, 404), ("https://fc.yahoo.com", 500)]
)
def test_cookie_exception_does_not_weaken_other_status_failures(url: str, status: int) -> None:
    """Only a 404 from the approved basic-cookie endpoint is optional."""
    exchange = _SyntheticExchange([status])
    session = CoordinatedSession(exchange=exchange, expected_symbol="7203.T")
    if status == 429:
        with pytest.raises(CoordinatedSessionError, match="rate_limited"):
            session.get(url)
    else:
        session.get(url)
    assert session.poisoned


def test_one_status_fallback_requires_strategy_change() -> None:
    """A changed crumb strategy permits exactly one repeated chart send."""
    exchange = _SyntheticExchange([200, 404, 200, 200])
    session = CoordinatedSession(exchange=exchange, expected_symbol="7203.T")
    session.get(BASIC_CRUMB)
    assert session.get(CHART).status_code == 404
    session.get(CSRF_CRUMB)
    assert session.get(CHART).status_code == 200
    with pytest.raises(CoordinatedSessionError, match="unapproved_yahoo_repeat"):
        session.get(CHART)
    assert len(exchange.calls) == 4


def test_repeat_without_strategy_change_never_sends() -> None:
    """A plain retry is stopped before the exchange port is called."""
    exchange = _SyntheticExchange([404])
    session = CoordinatedSession(exchange=exchange, expected_symbol="7203.T")
    session.get(CHART)
    with pytest.raises(CoordinatedSessionError, match="unapproved_yahoo_status_fallback"):
        session.get(CHART)
    assert len(exchange.calls) == 1


def test_fallback_cannot_change_chart_target() -> None:
    """A second ticker or endpoint is outside the one approved status fallback."""
    exchange = _SyntheticExchange([404, 200])
    session = CoordinatedSession(exchange=exchange, expected_symbol="7203.T")
    session.get(CHART)
    session.get(CSRF_CRUMB)
    with pytest.raises(CoordinatedSessionError, match="unapproved_yahoo_endpoint"):
        session.get("https://query2.finance.yahoo.com/v8/finance/chart/6758.T")
    assert len(exchange.calls) == 2


def test_fallback_cannot_change_chart_date_range() -> None:
    """A status fallback can change its crumb, not its requested market-data window."""
    exchange = _SyntheticExchange([404, 200])
    session = CoordinatedSession(exchange=exchange, expected_symbol="7203.T")
    session.get(CHART, params={"period1": 1, "period2": 2, "crumb": "first"})
    session.get(CSRF_CRUMB)
    with pytest.raises(CoordinatedSessionError, match="unapproved_yahoo_status_fallback"):
        session.get(CHART, params={"period1": 1, "period2": 3, "crumb": "second"})
    assert len(exchange.calls) == 2


def test_retry_setting_cannot_be_enabled() -> None:
    """A mutated underlying retry strategy is stopped before any send."""
    exchange = _SyntheticExchange([])
    session = CoordinatedSession(exchange=exchange, expected_symbol="7203.T")
    session.retry.count = 1
    with pytest.raises(CoordinatedSessionError, match="yahoo_retry_enabled"):
        session.get(CHART)
    assert exchange.calls == []


def test_response_without_raw_publication_is_never_released() -> None:
    """A validated provider object alone is insufficient for yfinance release."""

    class _UnpublishedExchange:
        def exchange(self, identity: YahooRequestIdentity, request: EphemeralYahooRequest) -> YahooExchangeReceipt:
            return YahooExchangeReceipt(200, _Response(200), None)

    session = CoordinatedSession(exchange=_UnpublishedExchange(), expected_symbol="7203.T")
    with pytest.raises(CoordinatedSessionError, match="invalid_yahoo_exchange_receipt"):
        session.get(CHART)
    assert session.poisoned


def test_session_does_not_log_query_or_crumb(caplog: pytest.LogCaptureFixture) -> None:
    """The session's own boundary emits no transient request values."""
    exchange = _SyntheticExchange([200])
    session = CoordinatedSession(exchange=exchange, expected_symbol="7203.T")
    with caplog.at_level(logging.DEBUG):
        session.get(CHART, params={"crumb": "private-crumb-canary"})
    assert "private-crumb-canary" not in caplog.text
    assert "crumb" not in caplog.text


@pytest.mark.parametrize("status", [302, 429])
def test_redirect_or_rate_limit_poison_before_further_send(status: int) -> None:
    """Redirects and rate limits prevent the next physical send."""
    exchange = _SyntheticExchange([status])
    session = CoordinatedSession(exchange=exchange, expected_symbol="7203.T")
    with pytest.raises(CoordinatedSessionError):
        session.get(CHART)
    with pytest.raises(CoordinatedSessionError, match="yahoo_session_poisoned"):
        session.get(CHART)
    assert len(exchange.calls) == 1


def test_unknown_outcome_poisoned_even_if_upstream_catches_exception() -> None:
    """An uncertain send permanently closes the session."""
    exchange = _SyntheticExchange([RuntimeError("secret canary")])
    session = CoordinatedSession(exchange=exchange, expected_symbol="7203.T")
    with pytest.raises(CoordinatedSessionError, match="yahoo_exchange_failed") as error:
        session.get(BASIC_CRUMB)
    assert "canary" not in str(error.value)
    with pytest.raises(CoordinatedSessionError, match="yahoo_session_poisoned"):
        session.get(CHART)
    assert len(exchange.calls) == 1


@pytest.mark.parametrize(
    ("method", "url"),
    [
        ("GET", "https://evil.example/v8/finance/chart/7203.T"),
        ("GET", "http://query2.finance.yahoo.com/v8/finance/chart/7203.T"),
        ("GET", "https://query2.finance.yahoo.com/v8/finance/chart/7203.T/extra"),
        ("POST", CHART),
        ("GET", "https://finance.yahoo.com/"),
    ],
)
def test_unapproved_endpoint_rejected_before_exchange(method: str, url: str) -> None:
    """An unknown origin, path, or method never reaches the port."""
    exchange = _SyntheticExchange([])
    session = CoordinatedSession(exchange=exchange, expected_symbol="7203.T")
    with pytest.raises(CoordinatedSessionError, match="unapproved_yahoo_endpoint"):
        session.request(method, url)
    assert exchange.calls == []


def test_observed_consent_endpoints_are_individually_exchanged() -> None:
    """Pinned CSRF consent endpoints each require their own exchange."""
    exchange = _SyntheticExchange([200, 200, 200])
    session = CoordinatedSession(exchange=exchange, expected_symbol="7203.T")
    session.get("https://guce.yahoo.com/consent")
    session.post("https://consent.yahoo.com/v2/collectConsent?sessionId=secret", data={"csrfToken": "secret"})
    session.get("https://guce.yahoo.com/copyConsent?sessionId=secret")
    assert [call[0].endpoint for call in exchange.calls] == ["consent_form", "consent_collect", "consent_copy"]
    assert "secret" not in repr(exchange.calls)
