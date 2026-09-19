"""Deterministic security checks for machine-readable artifacts."""

import re
from collections.abc import Collection

from pydantic import JsonValue

from stock_research_llm_orchestrator.contracts.errors import ErrorCategory, ErrorCode
from stock_research_llm_orchestrator.contracts.validation.issues import RuleValidationError, ValidationIssue


FORBIDDEN_SECRET_KEYS = frozenset({"apikey", "accesstoken", "password", "privatekey", "secret"})
FORBIDDEN_EXTERNAL_INSTRUCTION_KEYS = frozenset(
    {"externalinstruction", "instructionsfromsource", "promptfromsource", "untrustedsystemprompt"}
)


def ensure_no_secret_fields(value: JsonValue, *, instance_path: str = "") -> None:
    """Reject explicit secret-bearing field names without copying their values."""
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{instance_path}/{_escape_pointer_token(key)}"
            if _normalized_key(key) in FORBIDDEN_SECRET_KEYS:
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


def ensure_external_content_is_data(value: JsonValue, *, instance_path: str = "") -> None:
    """Reject fields that promote source content into an Agent instruction channel."""
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{instance_path}/{_escape_pointer_token(key)}"
            if _normalized_key(key) in FORBIDDEN_EXTERNAL_INSTRUCTION_KEYS:
                raise RuleValidationError(
                    ValidationIssue(
                        category=ErrorCategory.SECURITY,
                        code=ErrorCode.EXTERNAL_INSTRUCTION_DETECTED,
                        message="external content must remain untrusted data, not Agent instructions",
                        instance_path=child_path,
                        context={"field_name": key},
                    )
                )
            ensure_external_content_is_data(child, instance_path=child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            ensure_external_content_is_data(child, instance_path=f"{instance_path}/{index}")


def ensure_permission_scope(requested: Collection[str], allowed: Collection[str]) -> None:
    """Reject requested permissions outside the role's applied allowlist."""
    disallowed = sorted(set(requested) - set(allowed))
    if disallowed:
        raise RuleValidationError(
            ValidationIssue(
                category=ErrorCategory.SECURITY,
                code=ErrorCode.PERMISSION_SCOPE_VIOLATION,
                message="requested permission is outside the applied role scope",
                context={"disallowed_permissions": ",".join(disallowed)},
            )
        )


def _escape_pointer_token(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _normalized_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.casefold())
