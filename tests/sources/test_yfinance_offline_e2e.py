"""Pinned yfinance compatibility using a test-only exchange, never live authority."""

import json
from datetime import date
from typing import Any, cast

import pandas as pd  # type: ignore[import-untyped]
import pytest
import yfinance as yf  # type: ignore[import-untyped]
import yfinance.cache as yf_cache  # type: ignore[import-untyped]
from curl_cffi import requests

from stock_research_llm_orchestrator.requests.production import LogicalResultOutcome
from stock_research_llm_orchestrator.sources.jpx.current_list import JpxListedIssue
from stock_research_llm_orchestrator.sources.jpx.verification import JpxSecurityVerification
from stock_research_llm_orchestrator.sources.yfinance import (
    YfinanceDailyAdapter,
    map_jpx_verification_to_yfinance_daily,
)
from stock_research_llm_orchestrator.sources.yfinance.coordinated_session import (
    CoordinatedSession,
    EphemeralYahooRequest,
    YahooExchangeReceipt,
    YahooRequestIdentity,
)
from stock_research_llm_orchestrator.sources.yfinance.private_runtime import private_yfinance_runtime


def _chart_body() -> bytes:
    return json.dumps(
        {
            "chart": {
                "error": None,
                "result": [
                    {
                        "meta": {
                            "currency": "JPY",
                            "symbol": "1301.T",
                            "exchangeName": "JPX",
                            "fullExchangeName": "Tokyo",
                            "instrumentType": "EQUITY",
                            "firstTradeDate": 1000000000,
                            "regularMarketTime": 1788220800,
                            "gmtoffset": 32400,
                            "timezone": "JST",
                            "exchangeTimezoneName": "Asia/Tokyo",
                            "regularMarketPrice": 100.0,
                            "chartPreviousClose": 99.0,
                            "priceHint": 2,
                            "currentTradingPeriod": {},
                            "dataGranularity": "1d",
                            "range": "1mo",
                            "validRanges": ["1d", "1mo"],
                        },
                        "timestamp": [1788220800],
                        "indicators": {
                            "quote": [
                                {
                                    "open": [100.0],
                                    "high": [102.0],
                                    "low": [99.0],
                                    "close": [101.0],
                                    "volume": [1000],
                                }
                            ],
                            "adjclose": [{"adjclose": [101.0]}],
                        },
                        "events": {},
                    }
                ],
            }
        },
        separators=(",", ":"),
    ).encode()


class _Response:
    def __init__(self, url: str, body: bytes, media_type: str) -> None:
        self.status_code = 200
        self.url = url
        self.content = body
        self.text = body.decode()
        self.headers = {"Content-Type": media_type}
        self.cookies: dict[str, str] = {}

    def json(self) -> object:
        return json.loads(self.content)


def test_real_download_routes_every_send_through_injected_exchange(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise upstream cookie/crumb/chart behavior without authorizing production auxiliary sends."""

    class _EmptyTimezoneCache:
        def lookup(self, key: str) -> None:
            return None

        def store(self, key: str, value: str | None) -> None:
            pass

    monkeypatch.setattr(yf_cache, "get_tz_cache", lambda: _EmptyTimezoneCache())

    def deny_wire(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("direct_network_attempt")

    monkeypatch.setattr(requests.Session, "request", deny_wire)

    chart = _chart_body()
    sent: list[str] = []

    def backend(request: EphemeralYahooRequest, limit: int) -> _Response:
        assert request.kwargs["allow_redirects"] is False
        assert limit >= len(chart)
        if request.url == "https://fc.yahoo.com":
            body, media_type = b"cookie", "text/plain"
            kind = "cookie"
        elif request.url == "https://query1.finance.yahoo.com/v1/test/getcrumb":
            body, media_type = b"offline-crumb", "text/plain"
            kind = "crumb"
        elif request.url == "https://query2.finance.yahoo.com/v8/finance/chart/1301.T":
            body, media_type = chart, "application/json"
            kind = "chart"
        else:
            raise AssertionError("unapproved_send")
        sent.append(kind)
        return _Response(request.url, body, media_type)

    class _CompatibilityExchange:
        """Artificial receipts only; durable chart publication is tested against the production port separately."""

        def __init__(self) -> None:
            self.outcome: LogicalResultOutcome | None = None
            self.receipts: list[YahooExchangeReceipt] = []

        def exchange(self, identity: YahooRequestIdentity, request: EphemeralYahooRequest) -> YahooExchangeReceipt:
            response = backend(request, 100000)
            receipt = YahooExchangeReceipt(response.status_code, response, object())
            self.receipts.append(receipt)
            return receipt

        def finalize(self, outcome: LogicalResultOutcome, error_code: str | None = None) -> None:
            assert self.outcome is None
            assert error_code is None
            self.outcome = outcome

    port = _CompatibilityExchange()
    verification = JpxSecurityVerification(
        requested_code="1301",
        snapshot_on="2026-08-31",
        status="verified_eligible",
        issue=JpxListedIssue(
            snapshot_on="2026-08-31",
            code="1301",
            name="eligible",
            market_product_category="プライム（内国株式）",
            issuer_domesticity="domestic",
            security_class="ordinary_common_equity",
            eligibility="eligible",
            market_segment="Prime",
        ),
    )
    intent = map_jpx_verification_to_yfinance_daily(verification, start=date(2026, 9, 1), end=date(2026, 9, 3))
    session = CoordinatedSession(exchange=port, expected_symbol=intent.symbol, intent=intent)
    invocation: list[dict[str, object]] = []

    def real_download(**kwargs: object) -> object:
        invocation.append(kwargs)
        return yf.download(**kwargs)

    # Retained local cookies must not skip the synthetic cookie exchange.
    with private_yfinance_runtime():
        result = cast("pd.DataFrame", YfinanceDailyAdapter(download=real_download).download(intent, session=session))

    assert not result.empty
    assert list(result["Close"]["1301.T"]) == [101.0]
    assert invocation == [
        {
            "tickers": ["1301.T"],
            "start": "2026-09-01",
            "end": "2026-09-03",
            "interval": "1d",
            "threads": False,
            "repair": False,
            "keepna": True,
            "progress": False,
            "actions": True,
            "auto_adjust": False,
            "session": session,
        }
    ]
    assert sent.count("chart") == 2
    assert sent.count("crumb") >= 1
    assert sent.count("cookie") >= 1
    assert len(port.receipts) == len(sent)
    assert len({id(receipt.raw_reference) for receipt in port.receipts}) == len(sent)
    expected_bodies = {"cookie": b"cookie", "crumb": b"offline-crumb", "chart": chart}
    for receipt, kind in zip(port.receipts, sent, strict=True):
        expected_body = expected_bodies[kind]
        assert cast("_Response", receipt.response).content == expected_body
    assert port.outcome is LogicalResultOutcome.SUCCEEDED
