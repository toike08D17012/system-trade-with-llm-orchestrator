"""Pinned yfinance HTTP boundary. No network call occurs without an exchange port."""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from curl_cffi import requests

from stock_research_llm_orchestrator.requests.production import LogicalResultOutcome
from stock_research_llm_orchestrator.sources.yfinance.models import YfinanceDailyIntent


class CoordinatedSessionError(RuntimeError):
    """Sanitized fail-closed session error."""


@dataclass(frozen=True)
class YahooRequestIdentity:
    """Secret-free classification for one physical request."""

    method: str
    origin: str
    resource_class: str
    endpoint: str


@dataclass(frozen=True)
class EphemeralYahooRequest:
    """In-memory arguments consumed once by the controlled transport bridge."""

    method: str
    url: str = field(repr=False)
    kwargs: dict[str, object] = field(repr=False)


@dataclass(frozen=True)
class YahooExchangeReceipt:
    """Response released only after validation and synchronous raw publication."""

    status_code: int
    response: object = field(repr=False)
    raw_reference: object = field(repr=False)
    audit_reference: object | None = field(default=None, repr=False)


class YahooExchangePort(Protocol):
    """Coordinator-owned per-send bridge; each call must acquire gates and permit."""

    def exchange(self, identity: YahooRequestIdentity, request: EphemeralYahooRequest) -> YahooExchangeReceipt:
        """Execute one coordinator-permitted send and synchronously publish its raw bytes."""
        ...


_CRUMB_PATH = "/v1/test/getcrumb"
_CHART_PREFIX = "/v8/finance/chart/"
_ALLOWED_KWARGS = {"params", "data", "json", "headers", "cookies", "timeout", "allow_redirects"}


def _classify(method: str, url: str, expected_symbol: str) -> YahooRequestIdentity:
    try:
        parsed = urlsplit(url)
        origin = parsed.hostname or ""
        if (
            parsed.scheme != "https"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port is not None
            or parsed.fragment
            or parsed.netloc != origin
        ):
            raise ValueError
    except ValueError:
        raise CoordinatedSessionError("unapproved_yahoo_endpoint") from None

    path = parsed.path
    if method == "GET" and origin == "fc.yahoo.com" and path in {"", "/"}:
        resource_class = "cookie"
        endpoint = "cookie_basic"
    elif method == "GET" and origin in {"query1.finance.yahoo.com", "query2.finance.yahoo.com"} and path == _CRUMB_PATH:
        resource_class = "crumb"
        endpoint = "crumb_basic" if origin.startswith("query1") else "crumb_csrf"
    elif method == "GET" and origin == "query2.finance.yahoo.com" and path.startswith(_CHART_PREFIX):
        symbol = path[len(_CHART_PREFIX) :]
        if symbol != expected_symbol:
            raise CoordinatedSessionError("unapproved_yahoo_endpoint")
        resource_class = "chart"
        endpoint = "chart"
    elif method == "GET" and origin == "guce.yahoo.com" and path == "/consent":
        resource_class = "consent"
        endpoint = "consent_form"
    elif method == "POST" and origin == "consent.yahoo.com" and path == "/v2/collectConsent":
        resource_class = "consent"
        endpoint = "consent_collect"
    elif method == "GET" and origin == "guce.yahoo.com" and path == "/copyConsent":
        resource_class = "consent"
        endpoint = "consent_copy"
    else:
        raise CoordinatedSessionError("unapproved_yahoo_endpoint")
    return YahooRequestIdentity(method, origin, resource_class, endpoint)


class CoordinatedSession(requests.Session):
    """Intercept every yfinance request and enforce a bounded status fallback."""

    def __init__(
        self, *, exchange: YahooExchangePort, expected_symbol: str, intent: YfinanceDailyIntent | None = None
    ) -> None:
        """Require a coordinator-owned exchange port for all physical sends."""
        if not callable(getattr(exchange, "exchange", None)):
            raise TypeError("coordinator_exchange_required")
        if re.fullmatch(r"[0-9A-Z]{4}\.T", expected_symbol) is None:
            raise ValueError("invalid_yahoo_symbol")
        super().__init__()
        self.retry.count = 0
        self._exchange = exchange
        self._expected_symbol = expected_symbol
        self._intent = None if intent is None else YfinanceDailyIntent.model_validate(intent.model_dump(warnings=False))
        if self._intent is not None and self._intent.symbol != expected_symbol:
            raise CoordinatedSessionError("yahoo_session_intent_mismatch")
        self._active_request: EphemeralYahooRequest | None = None
        self._active_identity: YahooRequestIdentity | None = None
        self._active_snapshot: EphemeralYahooRequest | None = None
        self._envelope_consumed = False
        self._poisoned = False
        self._pending_fallback: tuple[str, str, str, tuple[tuple[str, str], ...]] | None = None
        self._fallback_used = False
        self._strategy = "basic"
        self._failed_strategy: str | None = None
        self._chart_sent: set[str] = set()
        self._finalized = False
        bind_session = getattr(exchange, "bind_session", None)
        if callable(bind_session):
            bind_session(self)

    def assert_intent(self, intent: YfinanceDailyIntent, *, require_bound: bool = False) -> None:
        """Compare complete bindings while retaining symbol-only artificial sessions."""
        snapshot = YfinanceDailyIntent.model_validate(intent.model_dump(warnings=False))
        if self._expected_symbol != snapshot.symbol or (
            self._intent != snapshot and (require_bound or self._intent is not None)
        ):
            raise CoordinatedSessionError("yahoo_session_intent_mismatch")

    def assert_exchange_binding(self, exchange: YahooExchangePort, intent: YfinanceDailyIntent) -> None:
        """Require the exact production session and its complete intent."""
        self.assert_intent(intent, require_bound=True)
        if self._exchange is not exchange or self._poisoned or self._finalized or self.retry.count != 0:
            raise CoordinatedSessionError("yahoo_session_binding_mismatch")

    def assert_active_envelope(
        self,
        exchange: YahooExchangePort,
        identity: YahooRequestIdentity,
        request: EphemeralYahooRequest,
        *,
        consume: bool,
    ) -> None:
        """Admit the currently dispatched envelope once, then permit read-only rechecks."""
        if (
            self._exchange is not exchange
            or self._poisoned
            or self._finalized
            or self.retry.count != 0
            or request is not self._active_request
            or identity != self._active_identity
            or request != self._active_snapshot
            or self._envelope_consumed == consume
        ):
            self._poisoned = True
            raise CoordinatedSessionError("yahoo_session_envelope_mismatch")
        self._validate_bound_chart(identity, request.url, request.kwargs)
        if consume:
            self._envelope_consumed = True

    def _validate_bound_chart(self, identity: YahooRequestIdentity, url: str, kwargs: dict[str, object]) -> None:
        if self._intent is None or identity.resource_class != "chart":
            return
        if (
            _classify(identity.method, url, self._intent.symbol) != identity
            or urlsplit(url).query
            or set(kwargs) - {"params", "timeout", "allow_redirects"}
        ):
            raise CoordinatedSessionError("unapproved_yahoo_chart_parameters")
        params = kwargs.get("params")
        if type(params) is not dict:
            raise CoordinatedSessionError("unapproved_yahoo_chart_parameters")
        expected: dict[str, object]
        if params.get("range") == "1d":
            expected = {"range": "1d", "interval": "1d"}
        else:
            expected = {
                "period1": int(
                    datetime.fromisoformat(self._intent.start).replace(tzinfo=ZoneInfo("Asia/Tokyo")).timestamp()
                ),
                "period2": int(
                    datetime.fromisoformat(self._intent.end).replace(tzinfo=ZoneInfo("Asia/Tokyo")).timestamp()
                ),
                "interval": "1d",
                "includePrePost": False,
                "events": "div,splits,capitalGains",
            }
        target = {key: value for key, value in params.items() if key != "crumb"}
        if (
            target != expected
            or any(type(target[key]) is not type(value) for key, value in expected.items())
            or ("crumb" in params and not isinstance(params["crumb"], str))
        ):
            raise CoordinatedSessionError("unapproved_yahoo_chart_parameters")

    @property
    def poisoned(self) -> bool:
        """Whether further sends are prohibited after a terminal anomaly."""
        return self._poisoned

    @property
    def expected_symbol(self) -> str:
        """Return the verified symbol bound to this session."""
        return self._expected_symbol

    @property
    def complete(self) -> bool:
        """Whether one chart response completed without an unresolved fallback."""
        return "history" in self._chart_sent and self._pending_fallback is None and not self._poisoned

    @property
    def uncertain(self) -> bool:
        """Whether the exchange port observed a send with unknown outcome."""
        return bool(getattr(self._exchange, "uncertain", False))

    def finalize(self, outcome: LogicalResultOutcome, error_code: str | None = None) -> None:
        """Finish the bound logical request exactly once after the library call."""
        if self._finalized:
            raise CoordinatedSessionError("yahoo_session_already_finalized")
        finalize = getattr(self._exchange, "finalize", None)
        if not callable(finalize):
            raise CoordinatedSessionError("yahoo_exchange_finalization_missing")
        if outcome is LogicalResultOutcome.SUCCEEDED and not self.complete:
            raise CoordinatedSessionError("yahoo_session_incomplete")
        finalize(outcome, error_code)
        self._finalized = True
        self._poisoned = True

    def request(self, method: str, url: str, **kwargs: object) -> object:  # type: ignore[override]
        """Classify before the coordinator-owned bridge performs one physical send."""
        if self._poisoned or self._finalized or self._active_request is not None:
            self._poisoned = True
            raise CoordinatedSessionError("yahoo_session_poisoned")
        if self.retry.count != 0:
            self._poisoned = True
            raise CoordinatedSessionError("yahoo_retry_enabled")
        method = method.upper()
        if set(kwargs) - _ALLOWED_KWARGS:
            self._poisoned = True
            raise CoordinatedSessionError("unapproved_yahoo_request_arguments")
        try:
            kwargs = deepcopy(kwargs)
            identity = _classify(method, url, self._expected_symbol)
            self._validate_bound_chart(identity, url, kwargs)
            chart_kind = self._chart_kind(identity, kwargs)
            chart_target = self._chart_target(kwargs) if chart_kind is not None else ()
            self._check_transition(identity, url, chart_kind, chart_target)
        except CoordinatedSessionError:
            self._poisoned = True
            raise

        # yfinance 1.7.0 asks for redirects on cookie/crumb calls. The transport
        # always receives false; a 3xx response terminates this session.
        envelope = EphemeralYahooRequest(method, url, {**kwargs, "allow_redirects": False})
        self._active_request = envelope
        self._active_identity = identity
        self._active_snapshot = deepcopy(envelope)
        self._envelope_consumed = False
        try:
            receipt = self._exchange.exchange(identity, envelope)
        except Exception:
            self._poisoned = True
            raise CoordinatedSessionError("yahoo_exchange_failed") from None
        finally:
            self._active_request = None
            self._active_identity = None
            self._active_snapshot = None
        if (
            not isinstance(receipt, YahooExchangeReceipt)
            or (receipt.raw_reference is None and receipt.audit_reference is None)
            or (identity.resource_class == "chart" and receipt.raw_reference is None)
            or not isinstance(receipt.status_code, int)
            or not 100 <= receipt.status_code <= 599
            or getattr(receipt.response, "status_code", None) != receipt.status_code
            or not isinstance(getattr(receipt.response, "content", None), bytes)
            or not isinstance(getattr(receipt.response, "text", None), str)
            or not callable(getattr(receipt.response, "json", None))
            or getattr(receipt.response, "headers", None) is None
            or getattr(receipt.response, "cookies", None) is None
        ):
            self._poisoned = True
            raise CoordinatedSessionError("invalid_yahoo_exchange_receipt")
        status = receipt.status_code
        if status == 429:
            self._poisoned = True
            raise CoordinatedSessionError("yahoo_rate_limited")
        if 300 <= status < 400:
            self._poisoned = True
            raise CoordinatedSessionError("yahoo_redirect_rejected")
        if status >= 400 and not (identity.endpoint == "cookie_basic" and status == 404):
            if identity.resource_class == "chart" and not self._fallback_used and self._pending_fallback is None:
                assert chart_kind is not None
                self._pending_fallback = (identity.method, url, chart_kind, chart_target)
                self._failed_strategy = self._strategy
            else:
                self._poisoned = True
        elif identity.resource_class == "chart":
            self._pending_fallback = None
        if chart_kind is not None:
            self._chart_sent.add(chart_kind)
        return receipt.response

    def _chart_kind(self, identity: YahooRequestIdentity, kwargs: dict[str, object]) -> str | None:
        if identity.resource_class != "chart":
            return None
        params = kwargs.get("params")
        if not isinstance(params, dict):
            return "history"
        if params.get("range") == "1d" and params.get("interval") == "1d":
            if "period1" in params or "period2" in params:
                raise CoordinatedSessionError("unapproved_yahoo_chart_parameters")
            return "timezone"
        if "range" in params:
            raise CoordinatedSessionError("unapproved_yahoo_chart_parameters")
        return "history"

    def _chart_target(self, kwargs: dict[str, object]) -> tuple[tuple[str, str], ...]:
        params = kwargs.get("params")
        if not isinstance(params, dict):
            return ()
        return tuple(sorted((str(key), str(value)) for key, value in params.items() if key != "crumb"))

    def _check_transition(
        self,
        identity: YahooRequestIdentity,
        url: str,
        chart_kind: str | None,
        chart_target: tuple[tuple[str, str], ...],
    ) -> None:
        if identity.endpoint in {"crumb_basic", "cookie_basic"}:
            self._strategy = "basic"
        elif identity.endpoint in {"crumb_csrf", "consent_form", "consent_collect", "consent_copy"}:
            self._strategy = "csrf"
        if chart_kind is None:
            return
        if self._pending_fallback is None:
            if chart_kind in self._chart_sent:
                raise CoordinatedSessionError("unapproved_yahoo_repeat")
            return
        if (
            self._fallback_used
            or (identity.method, url, chart_kind, chart_target) != self._pending_fallback
            or self._strategy == self._failed_strategy
        ):
            raise CoordinatedSessionError("unapproved_yahoo_status_fallback")
        self._fallback_used = True
