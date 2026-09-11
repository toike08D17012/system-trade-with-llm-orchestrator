"""Policy reference validation helpers."""

from stock_research_llm_orchestrator.contracts.errors import ErrorCategory, ErrorCode
from stock_research_llm_orchestrator.contracts.validation.issues import RuleValidationError, ValidationIssue


def ensure_policy_version(expected: int, actual: int) -> None:
    """Reject implicit selection of a different policy version."""
    if expected != actual:
        raise RuleValidationError(
            ValidationIssue(
                category=ErrorCategory.POLICY,
                code=ErrorCode.POLICY_VERSION_MISMATCH,
                message="policy version does not match the referenced version",
                context={"expected_version": expected, "actual_version": actual},
            )
        )
