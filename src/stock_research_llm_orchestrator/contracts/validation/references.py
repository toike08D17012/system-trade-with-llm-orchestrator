"""Reference validation helpers."""

from collections.abc import Collection, Iterable

from stock_research_llm_orchestrator.contracts.errors import ErrorCategory, ErrorCode
from stock_research_llm_orchestrator.contracts.validation.issues import RuleValidationError, ValidationIssue


def ensure_references_exist(referenced_ids: Iterable[str], available_ids: Collection[str]) -> None:
    """Reject the first reference that is absent from the approved set."""
    for referenced_id in referenced_ids:
        if referenced_id not in available_ids:
            raise RuleValidationError(
                ValidationIssue(
                    category=ErrorCategory.REFERENCE,
                    code=ErrorCode.REFERENCE_NOT_FOUND,
                    message="referenced artifact does not exist",
                    context={"reference_id": referenced_id},
                )
            )
