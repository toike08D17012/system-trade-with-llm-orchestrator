"""Deterministic security verification against one JPX current-list snapshot."""

from typing import Literal

from pydantic import Field

from stock_research_llm_orchestrator.contracts.base import StrictContractModel
from stock_research_llm_orchestrator.sources.jpx.current_list import JpxCurrentList, JpxListedIssue


class JpxSecurityVerification(StrictContractModel):
    """Explicit result of looking up one exact security code."""

    requested_code: str = Field(pattern=r"^[0-9A-Z]{4}$")
    snapshot_on: str
    status: Literal["verified_eligible", "verified_ineligible", "not_found"]
    issue: JpxListedIssue | None


def verify_jpx_security(snapshot: JpxCurrentList, code: str) -> JpxSecurityVerification:
    """Verify an exact code without ticker rewriting, class inference, or fallback."""
    if not code.isascii() or len(code) != 4 or not code.isalnum() or code != code.upper():
        raise ValueError("invalid_jpx_security_code")
    matches = tuple(issue for issue in snapshot.issues if issue.code == code)
    if not matches:
        return JpxSecurityVerification(
            requested_code=code,
            snapshot_on=snapshot.snapshot_on,
            status="not_found",
            issue=None,
        )
    if len(matches) != 1:
        raise ValueError("ambiguous_jpx_security_code")
    issue = matches[0]
    return JpxSecurityVerification(
        requested_code=code,
        snapshot_on=snapshot.snapshot_on,
        status="verified_eligible" if issue.eligibility == "eligible" else "verified_ineligible",
        issue=issue,
    )
