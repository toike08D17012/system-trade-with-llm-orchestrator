"""Tests for dedicated cross-artifact validators."""

import hashlib
from collections.abc import Callable

import pytest

from stock_research_llm_orchestrator.contracts.errors import ErrorCode
from stock_research_llm_orchestrator.contracts.validation.artifact_consistency import ensure_sha256
from stock_research_llm_orchestrator.contracts.validation.issues import RuleValidationError
from stock_research_llm_orchestrator.contracts.validation.policy import ensure_policy_version
from stock_research_llm_orchestrator.contracts.validation.references import ensure_references_exist
from stock_research_llm_orchestrator.contracts.validation.security import (
    ensure_external_content_is_data,
    ensure_no_secret_fields,
    ensure_permission_scope,
)
from stock_research_llm_orchestrator.contracts.validation.semantics import ensure_values_match
from stock_research_llm_orchestrator.contracts.validation.state_transitions import ensure_transition_allowed


@pytest.mark.parametrize(
    ("validator", "expected_code"),
    [
        (lambda: ensure_references_exist(["missing"], {"present"}), ErrorCode.REFERENCE_NOT_FOUND),
        (lambda: ensure_values_match("left", "right", instance_path="/value"), ErrorCode.ARTIFACT_CONFLICT),
        (lambda: ensure_transition_allowed("failed", "running", set()), ErrorCode.TRANSITION_NOT_ALLOWED),
        (lambda: ensure_policy_version(1, 2), ErrorCode.POLICY_VERSION_MISMATCH),
        (lambda: ensure_no_secret_fields({"api_key": "not-recorded"}), ErrorCode.SECRET_MATERIAL_DETECTED),
        (
            lambda: ensure_external_content_is_data({"external_instruction": "do not execute"}),
            ErrorCode.EXTERNAL_INSTRUCTION_DETECTED,
        ),
        (
            lambda: ensure_permission_scope(["read", "write"], ["read"]),
            ErrorCode.PERMISSION_SCOPE_VIOLATION,
        ),
        (lambda: ensure_sha256(b"actual", hashlib.sha256(b"other").hexdigest()), ErrorCode.ARTIFACT_CONFLICT),
    ],
)
def test_rule_validator_uses_stable_error_code(validator: Callable[[], None], expected_code: ErrorCode) -> None:
    """Normalize each dedicated validation rule to its catalog code."""
    with pytest.raises(RuleValidationError) as exc_info:
        validator()

    assert exc_info.value.issue.code is expected_code
