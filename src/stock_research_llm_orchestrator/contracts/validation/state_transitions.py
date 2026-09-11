"""State transition validation helpers."""

from collections.abc import Collection

from stock_research_llm_orchestrator.contracts.errors import ErrorCategory, ErrorCode
from stock_research_llm_orchestrator.contracts.validation.issues import RuleValidationError, ValidationIssue


def ensure_transition_allowed(current: str, target: str, allowed: Collection[tuple[str, str]]) -> None:
    """Reject a transition not explicitly present in the supplied state policy."""
    if (current, target) not in allowed:
        raise RuleValidationError(
            ValidationIssue(
                category=ErrorCategory.STATE_TRANSITION,
                code=ErrorCode.TRANSITION_NOT_ALLOWED,
                message="state transition is not allowed",
                context={"current_state": current, "target_state": target},
            )
        )
