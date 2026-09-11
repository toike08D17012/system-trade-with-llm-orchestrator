"""Semantic consistency helpers."""

from pydantic import JsonValue

from stock_research_llm_orchestrator.contracts.errors import ErrorCategory, ErrorCode
from stock_research_llm_orchestrator.contracts.validation.issues import RuleValidationError, ValidationIssue


def ensure_values_match(left: JsonValue, right: JsonValue, *, instance_path: str) -> None:
    """Reject conflicting projections of the same contract value."""
    if left != right:
        raise RuleValidationError(
            ValidationIssue(
                category=ErrorCategory.SEMANTIC,
                code=ErrorCode.ARTIFACT_CONFLICT,
                message="artifact values are inconsistent",
                instance_path=instance_path,
            )
        )
