"""Pure mapping from verified JPX identity to Yahoo Finance intent."""

from datetime import date

from stock_research_llm_orchestrator.sources.jpx.verification import JpxSecurityVerification
from stock_research_llm_orchestrator.sources.yfinance.models import YfinanceDailyIntent


def map_jpx_verification_to_yfinance_daily(
    verification: JpxSecurityVerification,
    *,
    start: date,
    end: date,
) -> YfinanceDailyIntent:
    """Build an exact Yahoo symbol without inference, rewriting, or fallback."""
    issue = verification.issue
    if (
        verification.status != "verified_eligible"
        or issue is None
        or verification.requested_code != issue.code
        or verification.snapshot_on != issue.snapshot_on
        or issue.mic != "XTKS"
        or issue.issuer_domesticity != "domestic"
        or issue.security_class != "ordinary_common_equity"
        or issue.eligibility != "eligible"
        or issue.market_segment not in ("Prime", "Standard", "Growth")
    ):
        raise ValueError("jpx_security_not_eligible_for_yfinance")
    return YfinanceDailyIntent(
        symbol=f"{issue.code}.T",
        jpx_code=issue.code,
        jpx_snapshot_on=verification.snapshot_on,
        mic="XTKS",
        market_segment=issue.market_segment,
        start=start.isoformat(),
        end=end.isoformat(),
    )
