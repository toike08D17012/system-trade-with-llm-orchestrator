"""Stable validation error contract."""

from enum import StrEnum
from typing import Literal

from pydantic import Field, JsonValue, model_validator

from stock_research_llm_orchestrator.contracts.base import NonEmptyString, StrictContractModel


class ErrorCategory(StrEnum):
    """Public validation error categories."""

    SCHEMA = "schema"
    SEMANTIC = "semantic"
    REFERENCE = "reference"
    STATE_TRANSITION = "state_transition"
    POLICY = "policy"
    SECURITY = "security"


class ErrorCode(StrEnum):
    """Stable error codes from the approved contract catalog."""

    INVALID_JSON = "invalid_json"
    UNSAFE_YAML = "unsafe_yaml"
    DUPLICATE_YAML_KEY = "duplicate_yaml_key"
    NON_JSON_YAML_VALUE = "non_json_yaml_value"
    UNKNOWN_SCHEMA = "unknown_schema"
    UNSUPPORTED_SCHEMA_VERSION = "unsupported_schema_version"
    REQUIRED_FIELD_MISSING = "required_field_missing"
    TYPE_MISMATCH = "type_mismatch"
    UNKNOWN_FIELD = "unknown_field"
    CONSTRAINT_VIOLATION = "constraint_violation"
    ARTIFACT_CONFLICT = "artifact_conflict"
    MARKDOWN_MISMATCH = "markdown_mismatch"
    REFERENCE_NOT_FOUND = "reference_not_found"
    REFERENCE_TYPE_MISMATCH = "reference_type_mismatch"
    REFERENCE_VERSION_MISMATCH = "reference_version_mismatch"
    TRANSITION_NOT_ALLOWED = "transition_not_allowed"
    CHECKPOINT_INVALID = "checkpoint_invalid"
    POLICY_NOT_FOUND = "policy_not_found"
    POLICY_VERSION_MISMATCH = "policy_version_mismatch"
    POLICY_VIOLATION = "policy_violation"
    SECRET_MATERIAL_DETECTED = "secret_material_detected"
    EXTERNAL_INSTRUCTION_DETECTED = "external_instruction_detected"
    PERMISSION_SCOPE_VIOLATION = "permission_scope_violation"


ERROR_CODES_BY_CATEGORY: dict[ErrorCategory, frozenset[ErrorCode]] = {
    ErrorCategory.SCHEMA: frozenset(
        {
            ErrorCode.INVALID_JSON,
            ErrorCode.UNSAFE_YAML,
            ErrorCode.DUPLICATE_YAML_KEY,
            ErrorCode.NON_JSON_YAML_VALUE,
            ErrorCode.UNKNOWN_SCHEMA,
            ErrorCode.UNSUPPORTED_SCHEMA_VERSION,
            ErrorCode.REQUIRED_FIELD_MISSING,
            ErrorCode.TYPE_MISMATCH,
            ErrorCode.UNKNOWN_FIELD,
            ErrorCode.CONSTRAINT_VIOLATION,
        }
    ),
    ErrorCategory.SEMANTIC: frozenset({ErrorCode.ARTIFACT_CONFLICT, ErrorCode.MARKDOWN_MISMATCH}),
    ErrorCategory.REFERENCE: frozenset(
        {
            ErrorCode.REFERENCE_NOT_FOUND,
            ErrorCode.REFERENCE_TYPE_MISMATCH,
            ErrorCode.REFERENCE_VERSION_MISMATCH,
        }
    ),
    ErrorCategory.STATE_TRANSITION: frozenset({ErrorCode.TRANSITION_NOT_ALLOWED, ErrorCode.CHECKPOINT_INVALID}),
    ErrorCategory.POLICY: frozenset(
        {ErrorCode.POLICY_NOT_FOUND, ErrorCode.POLICY_VERSION_MISMATCH, ErrorCode.POLICY_VIOLATION}
    ),
    ErrorCategory.SECURITY: frozenset(
        {
            ErrorCode.SECRET_MATERIAL_DETECTED,
            ErrorCode.EXTERNAL_INSTRUCTION_DETECTED,
            ErrorCode.PERMISSION_SCOPE_VIOLATION,
        }
    ),
}


class ValidationTargetV1(StrictContractModel):
    """Artifact and schema that failed validation."""

    artifact_type: NonEmptyString
    artifact_id: NonEmptyString | None = None
    schema_id: NonEmptyString
    schema_version: int = Field(ge=1)


class ValidationErrorV1(StrictContractModel):
    """One normalized validation failure."""

    schema_id: Literal["detailed-analysis.validation-error"]
    schema_version: Literal[1]
    error_contract_version: Literal[1]
    category: ErrorCategory = Field(strict=False)
    code: ErrorCode = Field(strict=False)
    target: ValidationTargetV1
    instance_path: str
    schema_path: str | None = None
    message: NonEmptyString
    context: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_category_and_code(self) -> ValidationErrorV1:
        """Reject code and category combinations not present in the catalog."""
        if self.code not in ERROR_CODES_BY_CATEGORY[self.category]:
            raise ValueError("validation error code does not belong to its category")
        return self
