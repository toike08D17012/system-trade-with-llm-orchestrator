"""Tests for the external request coordination audit contract."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.external_requests import (
    ExternalRequestEventV1,
    RequestOutcome,
    UsageAvailability,
)
from stock_research_llm_orchestrator.contracts.validation.wrapper import InputFormat, validate_text


FIXTURE_PATH = (
    Path(__file__).parents[1]
    / "fixtures"
    / "contracts"
    / "detailed-analysis"
    / "v1"
    / "external-request-event"
    / "valid"
    / "provider-failure.json"
)


def _payload() -> dict[str, object]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_provider_failure_records_cooldown_without_automatic_retry() -> None:
    """Retain logical, physical, gate, error, and usage audit data."""
    artifact = validate_text(FIXTURE_PATH.read_text(encoding="utf-8"), InputFormat.JSON)

    assert isinstance(artifact, ExternalRequestEventV1)
    assert artifact.outcome is RequestOutcome.FAILED
    assert artifact.automatic_retry_allowed is False
    assert artifact.direct_connection_fallback_allowed is False
    assert artifact.usage[1].availability is UsageAvailability.NOT_RETRIEVED
    assert artifact.usage[1].value is None


def test_unavailable_usage_cannot_be_replaced_with_zero() -> None:
    """Require an explicit unavailable value instead of a misleading zero."""
    payload = _payload()
    payload["usage"][1]["value"] = 0  # type: ignore[index]

    with pytest.raises(ValidationError, match="unavailable usage must have a null value"):
        ExternalRequestEventV1.model_validate(payload)


def test_agent_session_observability_cannot_claim_origin_control() -> None:
    """Avoid claiming a physical or origin-level control the CLI cannot expose."""
    payload = _payload()
    payload["event_type"] = "session_started"
    payload["observability"] = "agent_session_only"
    payload["physical_attempt_id"] = None
    payload["gate_results"][0]["scope"] = "origin"  # type: ignore[index]
    payload["outcome"] = "pending"
    payload["error"] = None

    with pytest.raises(ValidationError, match="must not claim origin-level control"):
        ExternalRequestEventV1.model_validate(payload)


def test_secret_bearing_fields_and_retry_relaxation_are_rejected() -> None:
    """Forbid credential values and non-approved retry behavior in audit data."""
    payload = _payload()
    payload["api_key"] = "secret-value"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ExternalRequestEventV1.model_validate(payload)

    payload = _payload()
    payload["automatic_retry_allowed"] = True
    with pytest.raises(ValidationError):
        ExternalRequestEventV1.model_validate(payload)
