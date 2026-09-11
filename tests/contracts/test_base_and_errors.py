"""Tests for strict base models and validation error contracts."""

import pytest
from pydantic import ValidationError

from stock_research_llm_orchestrator.contracts.base import ArtifactReferenceV1
from stock_research_llm_orchestrator.contracts.errors import (
    ErrorCategory,
    ErrorCode,
    ValidationErrorV1,
    ValidationTargetV1,
)


def test_artifact_reference_requires_exact_version_and_hash() -> None:
    """Accept only an exact schema version and lowercase SHA-256 digest."""
    reference = ArtifactReferenceV1(
        artifact_type="evidence-set",
        artifact_id="evidence-set-1",
        schema_id="detailed-analysis.evidence-set",
        schema_version=1,
        sha256="a" * 64,
    )

    assert reference.schema_version == 1
    with pytest.raises(ValidationError):
        ArtifactReferenceV1(
            artifact_type="evidence-set",
            artifact_id="evidence-set-1",
            schema_id="detailed-analysis.evidence-set",
            schema_version=1,
            sha256="not-a-digest",
        )


def _target() -> ValidationTargetV1:
    return ValidationTargetV1(artifact_type="task", schema_id="detailed-analysis.task", schema_version=1)


def test_validation_error_rejects_unknown_fields() -> None:
    """Reject fields that are not declared by the contract."""
    with pytest.raises(ValidationError):
        ValidationErrorV1.model_validate(
            {
                "schema_id": "detailed-analysis.validation-error",
                "schema_version": 1,
                "error_contract_version": 1,
                "category": "schema",
                "code": "invalid_json",
                "target": _target().model_dump(mode="json"),
                "instance_path": "",
                "message": "invalid input",
                "context": {},
                "unexpected": True,
            }
        )


def test_validation_error_rejects_type_coercion() -> None:
    """Reject scalar coercion in strict contract models."""
    with pytest.raises(ValidationError):
        ValidationTargetV1(artifact_type="task", schema_id="detailed-analysis.task", schema_version="1")  # type: ignore[arg-type]


def test_validation_error_rejects_code_from_another_category() -> None:
    """Reject error codes assigned to the wrong category."""
    with pytest.raises(ValidationError):
        ValidationErrorV1(
            schema_id="detailed-analysis.validation-error",
            schema_version=1,
            error_contract_version=1,
            category=ErrorCategory.SECURITY,
            code=ErrorCode.INVALID_JSON,
            target=_target(),
            instance_path="",
            message="invalid combination",
        )
