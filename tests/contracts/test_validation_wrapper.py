"""Tests for safe parsing and common validation dispatch."""

import json

import pytest

from stock_research_llm_orchestrator.contracts.errors import ErrorCode, ValidationErrorV1
from stock_research_llm_orchestrator.contracts.validation.wrapper import (
    ContractValidationError,
    InputFormat,
    validate_text,
)


def _valid_error() -> dict[str, object]:
    return {
        "schema_id": "detailed-analysis.validation-error",
        "schema_version": 1,
        "error_contract_version": 1,
        "category": "schema",
        "code": "invalid_json",
        "target": {
            "artifact_type": "task",
            "artifact_id": None,
            "schema_id": "detailed-analysis.task",
            "schema_version": 1,
        },
        "instance_path": "",
        "schema_path": None,
        "message": "input is not valid JSON",
        "context": {},
    }


def test_validate_json_dispatches_exact_contract() -> None:
    """Parse and dispatch a valid JSON artifact by exact version."""
    artifact = validate_text(json.dumps(_valid_error()), InputFormat.JSON)

    assert isinstance(artifact, ValidationErrorV1)


@pytest.mark.parametrize(
    ("text", "input_format", "expected_code"),
    [
        ("{", InputFormat.JSON, ErrorCode.INVALID_JSON),
        ('{"schema_id":"one","schema_id":"two"}', InputFormat.JSON, ErrorCode.INVALID_JSON),
        ('{"schema_id":"one","schema_version":NaN}', InputFormat.JSON, ErrorCode.INVALID_JSON),
        ('{"schema_id":"missing","schema_version":1}', InputFormat.JSON, ErrorCode.UNKNOWN_SCHEMA),
        (
            '{"schema_id":"detailed-analysis.validation-error","schema_version":2}',
            InputFormat.JSON,
            ErrorCode.UNSUPPORTED_SCHEMA_VERSION,
        ),
        (
            '{"schema_id":"detailed-analysis.validation-error","schema_version":0}',
            InputFormat.JSON,
            ErrorCode.CONSTRAINT_VIOLATION,
        ),
        ("schema_id: one\nschema_id: two\nschema_version: 1\n", InputFormat.YAML, ErrorCode.DUPLICATE_YAML_KEY),
        ("value: &shared [1]\ncopy: *shared\n", InputFormat.YAML, ErrorCode.UNSAFE_YAML),
        ("value: !!python/object:builtins.object {}\n", InputFormat.YAML, ErrorCode.UNSAFE_YAML),
    ],
)
def test_validate_text_normalizes_parse_and_dispatch_failures(
    text: str, input_format: InputFormat, expected_code: ErrorCode
) -> None:
    """Normalize parser and registry failures to stable error codes."""
    with pytest.raises(ContractValidationError) as exc_info:
        validate_text(text, input_format)

    assert exc_info.value.artifact.code is expected_code


def test_validate_text_maps_unknown_field_without_copying_value() -> None:
    """Do not copy a rejected field value into a public error artifact."""
    value = _valid_error()
    value["unexpected"] = "sensitive-value"

    with pytest.raises(ContractValidationError) as exc_info:
        validate_text(json.dumps(value), InputFormat.JSON)

    artifact = exc_info.value.artifact
    assert artifact.code is ErrorCode.UNKNOWN_FIELD
    assert "sensitive-value" not in artifact.model_dump_json()
