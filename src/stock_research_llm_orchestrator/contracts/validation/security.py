"""Deterministic security checks for machine-readable artifacts."""

from pydantic import JsonValue

from stock_research_llm_orchestrator.contracts.errors import ErrorCategory, ErrorCode
from stock_research_llm_orchestrator.contracts.validation.issues import RuleValidationError, ValidationIssue


FORBIDDEN_SECRET_KEYS = frozenset({"api_key", "access_token", "password", "private_key", "secret"})


def ensure_no_secret_fields(value: JsonValue, *, instance_path: str = "") -> None:
    """Reject explicit secret-bearing field names without copying their values."""
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{instance_path}/{_escape_pointer_token(key)}"
            if key.casefold() in FORBIDDEN_SECRET_KEYS:
                raise RuleValidationError(
                    ValidationIssue(
                        category=ErrorCategory.SECURITY,
                        code=ErrorCode.SECRET_MATERIAL_DETECTED,
                        message="secret-bearing field is not allowed in this artifact",
                        instance_path=child_path,
                        context={"field_name": key},
                    )
                )
            ensure_no_secret_fields(child, instance_path=child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            ensure_no_secret_fields(child, instance_path=f"{instance_path}/{index}")


def _escape_pointer_token(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")
