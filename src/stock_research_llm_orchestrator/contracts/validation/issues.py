"""Internal issue representation for dedicated validators."""

from dataclasses import dataclass, field

from pydantic import JsonValue

from stock_research_llm_orchestrator.contracts.errors import ErrorCategory, ErrorCode


@dataclass(frozen=True)
class ValidationIssue:
    """Library-independent validation issue before contract conversion."""

    category: ErrorCategory
    code: ErrorCode
    message: str
    instance_path: str = ""
    context: dict[str, JsonValue] = field(default_factory=dict)


class RuleValidationError(ValueError):
    """Raised by a dedicated validator with a stable issue."""

    def __init__(self, issue: ValidationIssue) -> None:
        """Store the normalized issue for the validation wrapper."""
        super().__init__(issue.message)
        self.issue = issue
