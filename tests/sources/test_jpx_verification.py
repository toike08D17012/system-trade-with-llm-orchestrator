"""Tests for exact JPX security verification."""

import pytest

from stock_research_llm_orchestrator.sources.jpx.current_list import JpxCurrentList, JpxListedIssue
from stock_research_llm_orchestrator.sources.jpx.verification import verify_jpx_security


def _snapshot() -> JpxCurrentList:
    return JpxCurrentList(
        snapshot_on="2026-08-31",
        issues=(
            JpxListedIssue(
                snapshot_on="2026-08-31",
                code="1301",
                name="eligible",
                market_product_category="プライム（内国株式）",
                eligibility="eligible",
                market_segment="Prime",
                issuer_domesticity="domestic",
                security_class="ordinary_common_equity",
            ),
            JpxListedIssue(
                snapshot_on="2026-08-31",
                code="1305",
                name="ineligible",
                market_product_category="ETF・ETN",
                eligibility="ineligible",
                market_segment=None,
                issuer_domesticity="unknown",
                security_class="unknown",
            ),
        ),
    )


def test_distinguishes_eligible_ineligible_and_absent_codes() -> None:
    """Keep source classification separate from absence in the current snapshot."""
    snapshot = _snapshot()
    eligible = verify_jpx_security(snapshot, "1301")
    ineligible = verify_jpx_security(snapshot, "1305")
    absent = verify_jpx_security(snapshot, "9999")

    assert eligible.status == "verified_eligible"
    assert eligible.issue is not None and eligible.issue.market_segment == "Prime"
    assert ineligible.status == "verified_ineligible"
    assert ineligible.issue is not None and ineligible.issue.security_class == "unknown"
    assert absent.status == "not_found"
    assert absent.issue is None


@pytest.mark.parametrize("code", ["01301", "1301.T", "１３０１", "abcd", ""])
def test_rejects_noncanonical_codes(code: str) -> None:
    """Reject ticker rewriting and noncanonical inputs."""
    with pytest.raises(ValueError, match="invalid_jpx_security_code"):
        verify_jpx_security(_snapshot(), code)
