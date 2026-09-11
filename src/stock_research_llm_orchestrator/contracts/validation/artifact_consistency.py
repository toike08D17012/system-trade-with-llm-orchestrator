"""Artifact content consistency helpers."""

import hashlib

from stock_research_llm_orchestrator.contracts.errors import ErrorCategory, ErrorCode
from stock_research_llm_orchestrator.contracts.validation.issues import RuleValidationError, ValidationIssue


def ensure_sha256(content: bytes, expected_hex_digest: str) -> None:
    """Reject content that does not match its immutable reference hash."""
    if hashlib.sha256(content).hexdigest() != expected_hex_digest:
        raise RuleValidationError(
            ValidationIssue(
                category=ErrorCategory.SEMANTIC,
                code=ErrorCode.ARTIFACT_CONFLICT,
                message="artifact content does not match its recorded hash",
            )
        )
