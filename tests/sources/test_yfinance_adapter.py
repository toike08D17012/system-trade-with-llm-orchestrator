"""No-network tests for the pinned yfinance invocation contract."""

import logging
from datetime import date
from types import SimpleNamespace

import pytest

from stock_research_llm_orchestrator.requests.production import LogicalResultOutcome
from stock_research_llm_orchestrator.sources.jpx.current_list import JpxListedIssue
from stock_research_llm_orchestrator.sources.jpx.verification import JpxSecurityVerification
from stock_research_llm_orchestrator.sources.yfinance import (
    YfinanceDailyAdapter,
    YfinanceDailyIntent,
    map_jpx_verification_to_yfinance_daily,
)
from stock_research_llm_orchestrator.sources.yfinance.coordinated_session import (
    CoordinatedSession,
    CoordinatedSessionError,
    EphemeralYahooRequest,
    YahooExchangeReceipt,
    YahooRequestIdentity,
)


class _Response:
    status_code = 200
    content = b"{}"
    text = "{}"
    headers: dict[str, str] = {}
    cookies: dict[str, str] = {}

    def json(self) -> dict[str, object]:
        return {}


class _Exchange:
    def __init__(self) -> None:
        self.finalized: tuple[LogicalResultOutcome, str | None] | None = None

    def exchange(self, identity: YahooRequestIdentity, request: EphemeralYahooRequest) -> YahooExchangeReceipt:
        return YahooExchangeReceipt(200, _Response(), object())

    def finalize(self, outcome: LogicalResultOutcome, error_code: str | None = None) -> None:
        self.finalized = (outcome, error_code)


def _session() -> CoordinatedSession:
    return CoordinatedSession(exchange=_Exchange(), expected_symbol="1301.T")


def _intent() -> YfinanceDailyIntent:
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
    return map_jpx_verification_to_yfinance_daily(verification, start=date(2026, 9, 1), end=date(2026, 9, 3))


def test_invokes_injected_download_once_with_exact_arguments_and_restores_retries() -> None:
    """Pin every argument and restore process-global retry state after success."""
    calls: list[dict[str, object]] = []
    result = object()
    config = SimpleNamespace(network=SimpleNamespace(retries=7))

    def download(**kwargs: object) -> object:
        calls.append(kwargs)
        assert config.network.retries == 0
        session = kwargs["session"]
        assert isinstance(session, CoordinatedSession)
        session.get("https://query2.finance.yahoo.com/v8/finance/chart/1301.T")
        return result

    session = _session()
    adapter = YfinanceDailyAdapter(download=download, config=config)

    assert adapter.download(_intent(), session=session) is result
    assert calls == [
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
    assert config.network.retries == 7


def test_restores_retries_when_download_raises() -> None:
    """Do not leave global retry policy altered after a provider failure."""
    config = SimpleNamespace(network=SimpleNamespace(retries=4))

    def download(**kwargs: object) -> object:
        session = kwargs["session"]
        assert isinstance(session, CoordinatedSession)
        session.get("https://query2.finance.yahoo.com/v8/finance/chart/1301.T")
        raise RuntimeError("synthetic failure")

    adapter = YfinanceDailyAdapter(download=download, config=config)
    with pytest.raises(ValueError, match="yfinance_download_failed"):
        adapter.download(_intent(), session=_session())
    assert config.network.retries == 4


def test_requires_session_and_exact_library_version_before_invocation() -> None:
    """Reject default transport and unreviewed library behavior."""
    calls = 0

    def download(**kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return object()

    adapter = YfinanceDailyAdapter(download=download)
    with pytest.raises(ValueError, match="yfinance_session_required"):
        adapter.download(_intent(), session=None)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="unsupported_yfinance_version"):
        YfinanceDailyAdapter(download=download, library_version="1.7.1")
    assert calls == 0


def test_metadata_records_source_function_period_and_fixed_options() -> None:
    """Expose enough source-native metadata to reproduce the invocation."""
    metadata = YfinanceDailyAdapter(download=lambda **kwargs: object()).metadata(_intent())

    assert metadata.model_dump() == {
        "library": "yfinance",
        "library_version": "1.7.0",
        "function": "download",
        "symbol": "1301.T",
        "start": "2026-09-01",
        "end": "2026-09-03",
        "interval": "1d",
        "threads": False,
        "repair": False,
        "keepna": True,
        "progress": False,
        "actions": True,
        "auto_adjust": False,
        "jpx_snapshot_on": "2026-08-31",
    }


def test_suppresses_yfinance_debug_values_during_download(caplog: pytest.LogCaptureFixture) -> None:
    """Keep upstream URL and crumb debug output out of application logs."""
    logger = logging.getLogger("yfinance")
    previous_level = logger.level

    def download(**kwargs: object) -> object:
        logger.debug("params={'crumb': 'secret-canary'}")
        session = kwargs["session"]
        assert isinstance(session, CoordinatedSession)
        session.get("https://query2.finance.yahoo.com/v8/finance/chart/1301.T")
        return object()

    with caplog.at_level(logging.DEBUG, logger="yfinance"):
        YfinanceDailyAdapter(download=download).download(_intent(), session=_session())

    assert "secret-canary" not in caplog.text
    assert logger.level == previous_level


@pytest.mark.parametrize(
    "field,value",
    [("start", "2026-08-01"), ("end", "2026-09-04"), ("jpx_snapshot_on", "2026-08-30"), ("market_segment", "Standard")],
)
def test_adapter_rejects_same_symbol_with_different_intent(field: str, value: str) -> None:
    """No library invocation occurs when any bound acquisition field differs."""
    intent = _intent()
    session = CoordinatedSession(exchange=_Exchange(), expected_symbol=intent.symbol, intent=intent)
    changed = YfinanceDailyIntent.model_validate({**intent.model_dump(), field: value})
    adapter = YfinanceDailyAdapter(download=lambda **kwargs: pytest.fail("unexpected_download"))
    with pytest.raises(CoordinatedSessionError, match="intent_mismatch"):
        adapter.download(changed, session=session)
