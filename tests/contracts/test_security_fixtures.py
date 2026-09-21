"""Tests for fixed security-boundary rejection fixtures."""

import json
import typing
from pathlib import Path

import pytest

from stock_research_llm_orchestrator.contracts.errors import ErrorCode
from stock_research_llm_orchestrator.contracts.validation.issues import RuleValidationError
from stock_research_llm_orchestrator.contracts.validation.security import (
    ensure_external_content_is_data,
    ensure_no_secret_fields,
    ensure_permission_scope,
)


FIXTURE_ROOT = (
    Path(__file__).parents[1] / "fixtures" / "contracts" / "detailed-analysis" / "v1" / "security" / "invalid"
)


def _payload(filename: str) -> dict[str, typing.Any]:
    parsed = json.loads((FIXTURE_ROOT / filename).read_text(encoding="utf-8"))
    assert isinstance(parsed, dict)
    assert all(isinstance(key, str) for key in parsed)
    return typing.cast("dict[str, typing.Any]", parsed)


def test_secret_material_fixture_is_rejected_without_value_disclosure() -> None:
    """Reject normalized secret-key variants and expose only the field name."""
    with pytest.raises(RuleValidationError) as captured:
        ensure_no_secret_fields(_payload("secret_material_detected.json"))

    assert captured.value.issue.code is ErrorCode.SECRET_MATERIAL_DETECTED
    assert "synthetic-fixture-secret" not in str(captured.value.issue)


def test_external_instruction_fixture_is_rejected_as_untrusted_data() -> None:
    """Prevent source content from entering an Agent instruction field."""
    with pytest.raises(RuleValidationError) as captured:
        ensure_external_content_is_data(_payload("external_instruction_detected.json"))

    assert captured.value.issue.code is ErrorCode.EXTERNAL_INSTRUCTION_DETECTED


def test_permission_fixture_is_rejected_outside_role_scope() -> None:
    """Reject a requested permission not present in the applied allowlist."""
    payload = _payload("permission_scope_violation.json")

    with pytest.raises(RuleValidationError) as captured:
        ensure_permission_scope(payload["requested"], payload["allowed"])  # type: ignore[arg-type]

    assert captured.value.issue.code is ErrorCode.PERMISSION_SCOPE_VIOLATION
