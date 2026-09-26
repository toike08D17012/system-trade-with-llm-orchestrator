"""Tests for strict JPX-to-Yahoo identity mapping."""

from datetime import date

import pytest
from pydantic import ValidationError

from stock_research_llm_orchestrator.sources.jpx.current_list import JpxListedIssue
from stock_research_llm_orchestrator.sources.jpx.verification import JpxSecurityVerification
from stock_research_llm_orchestrator.sources.yfinance import map_jpx_verification_to_yfinance_daily


def _verification(**issue_updates: object) -> JpxSecurityVerification:
    values: dict[str, object] = {
        "snapshot_on": "2026-08-31",
        "code": "1301",
        "name": "eligible",
        "market_product_category": "プライム（内国株式）",
        "mic": "XTKS",
        "issuer_domesticity": "domestic",
        "security_class": "ordinary_common_equity",
        "eligibility": "eligible",
        "market_segment": "Prime",
    }
    values.update(issue_updates)
    return JpxSecurityVerification(
        requested_code="1301",
        snapshot_on="2026-08-31",
        status="verified_eligible",
        issue=JpxListedIssue.model_validate(values),
    )


def test_maps_only_exact_verified_identity_and_preserves_snapshot() -> None:
    """Keep the JPX snapshot provenance alongside the exact `.T` mapping."""
    intent = map_jpx_verification_to_yfinance_daily(_verification(), start=date(2026, 9, 1), end=date(2026, 9, 3))

    assert intent.symbol == "1301.T"
    assert intent.jpx_code == "1301"
    assert intent.jpx_snapshot_on == "2026-08-31"
    assert intent.start == "2026-09-01"
    assert intent.end == "2026-09-03"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("issuer_domesticity", "unknown"),
        ("security_class", "unknown"),
        ("eligibility", "ineligible"),
        ("market_segment", None),
    ],
)
def test_rejects_noneligible_issue_traits(field: str, value: object) -> None:
    """Do not infer eligibility from the security code or market name."""
    with pytest.raises(ValueError, match="jpx_security_not_eligible_for_yfinance"):
        map_jpx_verification_to_yfinance_daily(
            _verification(**{field: value}), start=date(2026, 9, 1), end=date(2026, 9, 3)
        )


def test_rejects_status_and_issue_code_disagreement() -> None:
    """Reject a verification assembled from inconsistent source identities."""
    verification = _verification(code="1302")
    with pytest.raises(ValueError, match="jpx_security_not_eligible_for_yfinance"):
        map_jpx_verification_to_yfinance_daily(verification, start=date(2026, 9, 1), end=date(2026, 9, 3))


@pytest.mark.parametrize(
    ("start", "end"),
    [(date(2026, 9, 1), date(2026, 9, 1)), (date(2026, 9, 2), date(2026, 9, 1))],
)
def test_requires_nonempty_inclusive_exclusive_period(start: date, end: date) -> None:
    """Require a real daily interval with an exclusive end after start."""
    with pytest.raises(ValidationError, match="invalid_yfinance_daily_period"):
        map_jpx_verification_to_yfinance_daily(_verification(), start=start, end=end)
