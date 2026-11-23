"""Keep standard-route conditional quotes distinct from certified latest prices."""

import json
from hashlib import sha256

import pytest

from stock_research_llm_orchestrator.contracts.base import FileReferenceV1
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.acquisition_analysis import AcquiredEvidence
from stock_research_llm_orchestrator.research.price_observation import standard_price_observation


EVALUATION = "2026-11-14T03:00:00Z"


def _evidence(
    *,
    on: str = "2026-11-13",
    close: str | None = "3418",
    currency: str = "JPY",
    identity: bool = True,
    retrieved: str = "2026-11-14T02:00:00Z",
) -> AcquiredEvidence:
    row = {
        "on": on,
        "open": "3400",
        "high": "3500",
        "low": "3300",
        "close": close,
        "adjusted_close": close,
        "volume": "100000",
        "dividends": "0",
        "stock_splits": "0",
    }
    normalized = {
        "currency": currency,
        "source_timezone": "Asia/Tokyo",
        "requested_period": {"start_date": "2023-11-14", "end_date": "2026-11-13"},
        "observed_period": {"start_date": "2026-11-13", "end_date": "2026-11-13"},
        "three_year_request": True,
        "trading_day_coverage": "unconfirmed",
        "rows": [row],
        "issues": [{"code": "trading_calendar_unconfirmed", "on": None, "field": None}],
    }
    text = json.dumps({"normalized_prices": normalized, "provider_identity_verified": identity})
    return AcquiredEvidence(
        evidence_id="yfinance-quote",
        source_id="yfinance",
        source_reference="yfinance:Ticker.history:7203.T",
        retrieved_at=retrieved,
        raw_reference=FileReferenceV1(
            artifact_id="raw-price",
            relative_path="prices.csv",
            media_type="text/csv",
            sha256=sha256(b"prices").hexdigest(),
        ),
        text=text,
        validation="pending",
    )


def test_pending_standard_observation_supports_only_explicit_conditional_provenance() -> None:
    """Whole-series coverage gaps remain disclosed when using a parsed price observation."""
    quote = standard_price_observation((_evidence(),), EVALUATION)
    assert quote is not None
    assert quote["price"] == "3418"
    assert quote["on"] == "2026-11-13"
    assert quote["source_reference"] == "yfinance:Ticker.history:7203.T"
    assert quote["provenance"] == "machine_parsed_native_yfinance_observation"
    assert quote["validation"] == "pending"
    assert quote["limitations"]


@pytest.mark.parametrize(
    "changes",
    [
        {"identity": False},
        {"currency": "USD"},
        {"close": None},
        {"close": "0"},
        {"close": "-1"},
        {"on": "2026-11-14"},
        {"on": "2026-11-15"},
        {"retrieved": "2026-11-14T04:00:00Z"},
    ],
)
def test_ineligible_standard_price_is_not_adopted(changes: dict[str, object]) -> None:
    """Wrong identity, currency, missing prices and future information cannot enter calculations."""
    evidence = _evidence()
    payload = json.loads(evidence.text)
    if "identity" in changes:
        payload["provider_identity_verified"] = changes["identity"]
    if "currency" in changes:
        payload["normalized_prices"]["currency"] = changes["currency"]
    for key in ("close", "on"):
        if key in changes:
            payload["normalized_prices"]["rows"][0][key] = changes[key]
    evidence = evidence.model_copy(
        update={"text": json.dumps(payload), "retrieved_at": changes.get("retrieved", evidence.retrieved_at)}
    )
    assert standard_price_observation((evidence,), EVALUATION) is None


def test_rejected_evidence_is_not_used_even_with_plausible_price() -> None:
    """A plausible numeric quote cannot override rejected source validation."""
    evidence = _evidence().model_copy(update={"validation": "rejected"})
    assert standard_price_observation((evidence,), EVALUATION) is None


def test_latest_eligible_observation_is_selected_by_observation_date() -> None:
    """Selection prioritizes the observation date instead of retrieval order."""
    older = _evidence(on="2026-11-12", close="3400", retrieved="2026-11-14T02:30:00Z")
    newer = _evidence()
    quote = standard_price_observation((newer, older), EVALUATION)
    assert quote is not None and quote["price"] == "3418"
