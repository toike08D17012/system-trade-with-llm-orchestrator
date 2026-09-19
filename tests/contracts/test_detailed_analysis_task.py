"""Tests for the version 1 detailed-analysis task contract."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.task import (
    AnalysisHorizon,
    DetailedAnalysisTaskV1,
    SecurityInformationV1,
)
from stock_research_llm_orchestrator.contracts.errors import ErrorCode
from stock_research_llm_orchestrator.contracts.validation.wrapper import (
    ContractValidationError,
    InputFormat,
    validate_text,
)


FIXTURE_ROOT = (
    Path(__file__).parents[1] / "fixtures" / "contracts" / "detailed-analysis" / "v1" / "detailed-analysis-task"
)
NESTED_FIXTURE_ROOT = FIXTURE_ROOT.parent / "nested"


def _fixture_text(relative_path: str) -> str:
    return (FIXTURE_ROOT / relative_path).read_text(encoding="utf-8")


def test_valid_human_selected_task_is_registered_and_immutable() -> None:
    """Accept the approved initial task boundary through the common wrapper."""
    artifact = validate_text(_fixture_text("valid/human-selected.json"), InputFormat.JSON)

    assert isinstance(artifact, DetailedAnalysisTaskV1)
    assert artifact.analysis_horizons == (AnalysisHorizon.MEDIUM_TERM, AnalysisHorizon.LONG_TERM)
    with pytest.raises(ValidationError):
        artifact.task_id = "replacement"  # type: ignore[misc]


def test_explicit_human_document_language_override_is_accepted() -> None:
    """Accept an explicit language, target document, and reason."""
    artifact = validate_text(_fixture_text("valid/language-override.json"), InputFormat.JSON)

    assert isinstance(artifact, DetailedAnalysisTaskV1)
    assert artifact.human_document_language_override is not None
    assert artifact.human_document_language_override.language == "en-US"


@pytest.mark.parametrize(
    ("relative_path", "expected_code"),
    [
        ("invalid/constraint_violation/single-horizon.json", ErrorCode.CONSTRAINT_VIOLATION),
        ("invalid/unknown_field/information-cutoff.json", ErrorCode.UNKNOWN_FIELD),
    ],
)
def test_invalid_task_fixtures_are_rejected(relative_path: str, expected_code: ErrorCode) -> None:
    """Reject unsupported horizon narrowing and arbitrary information cutoffs."""
    with pytest.raises(ContractValidationError) as exc_info:
        validate_text(_fixture_text(relative_path), InputFormat.JSON)

    assert exc_info.value.artifact.code is expected_code


def test_task_rejects_offset_free_timestamp_and_safety_relaxation() -> None:
    """Require an offset timestamp and non-relaxable safety constraints."""
    payload = json.loads(_fixture_text("valid/human-selected.json"))
    payload["task_accepted_at"] = "2026-09-11T10:30:00"
    with pytest.raises(ValidationError):
        DetailedAnalysisTaskV1.model_validate(payload)

    payload = json.loads(_fixture_text("valid/human-selected.json"))
    payload["constraints"]["automated_trading_allowed"] = True
    with pytest.raises(ValidationError):
        DetailedAnalysisTaskV1.model_validate(payload)


def test_task_rejects_security_market_mismatch() -> None:
    """Require the security identifier to use the task market MIC."""
    payload = json.loads(_fixture_text("valid/human-selected.json"))
    payload["security"]["mic"] = "XNAS"

    with pytest.raises(ValidationError):
        DetailedAnalysisTaskV1.model_validate(payload)


def test_verified_security_information_keeps_market_attributes_separate() -> None:
    """Represent data-preparation attributes without changing the security identity."""
    payload = json.loads(
        (NESTED_FIXTURE_ROOT / "security-information" / "valid" / "verified.json").read_text(encoding="utf-8")
    )
    security = SecurityInformationV1.model_validate(payload)

    assert security.identifier.security_code == "7203"
    assert security.currency == "JPY"
