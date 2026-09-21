"""Tests for dispute, conditional audit, and human-decision contracts."""

import copy
import json
import typing
from pathlib import Path

import pytest
from pydantic import ValidationError

from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.dispute_audit import (
    AuditGateConditionsV1,
    AuditRequestV1,
    AuditResultV1,
    DisputeV1,
    HumanDecisionV1,
)
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.review import FindingClassification
from stock_research_llm_orchestrator.contracts.validation.review_audit import validate_logical_audit_history
from stock_research_llm_orchestrator.contracts.validation.wrapper import InputFormat, validate_text


FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "contracts" / "detailed-analysis" / "v1"


def _payload(artifact: str, filename: str) -> dict[str, typing.Any]:
    path = FIXTURE_ROOT / artifact / "valid" / filename
    parsed = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict)
    assert all(isinstance(key, str) for key in parsed)
    return typing.cast("dict[str, typing.Any]", parsed)


@pytest.mark.parametrize(
    ("artifact", "filename", "model"),
    [
        ("dispute", "material-policy-difference.json", DisputeV1),
        ("audit-request", "single-dispute.json", AuditRequestV1),
        ("audit-result", "support-first.json", AuditResultV1),
        ("human-decision", "workflow-direction.json", HumanDecisionV1),
    ],
)
def test_valid_dispute_audit_fixtures(artifact: str, filename: str, model: type[object]) -> None:
    """Dispatch all public dispute and audit artifacts through the common validator."""
    path = FIXTURE_ROOT / artifact / "valid" / filename

    result = validate_text(path.read_text(encoding="utf-8"), InputFormat.JSON)

    assert isinstance(result, model)


def test_finding_classification_contains_exact_approved_values() -> None:
    """Keep unclassified out of the terminal eight-value classification."""
    assert {item.value for item in FindingClassification} == {
        "factual_error",
        "insufficient_evidence",
        "schema_or_format_error",
        "policy_application_difference",
        "logical_gap",
        "future_hypothesis_difference",
        "risk_materiality_difference",
        "style_or_expression",
    }


@pytest.mark.parametrize(
    ("overrides", "decision"),
    [
        ({}, "start_audit"),
        ({"neutral_packet_valid": False}, "human_decision_required"),
        ({"already_validly_audited": True}, "human_decision_required"),
        ({"material": False}, "do_not_audit"),
        ({"classification_pending_present": True}, "do_not_audit"),
    ],
)
def test_audit_gate_decision_is_deterministic(overrides: dict[str, bool], decision: str) -> None:
    """Map the approved six conditions and pending guard to one exact result."""
    payload: dict[str, object] = {
        "primary_review_response_and_research_complete": True,
        "two_substantiated_positions_unresolved": True,
        "mechanically_resolvable": False,
        "material": True,
        "neutral_packet_valid": True,
        "already_validly_audited": False,
        "classification_pending_present": False,
        "decision": decision,
    }
    payload.update(overrides)

    assert AuditGateConditionsV1.model_validate(payload).decision.value == decision


def test_noninterpretive_finding_cannot_become_dispute() -> None:
    """Return factual and schema problems to deterministic correction paths."""
    payload = _payload("dispute", "material-policy-difference.json")
    payload["primary_classification"] = "factual_error"

    with pytest.raises(ValidationError, match="must not become audit disputes"):
        DisputeV1.model_validate(payload)


def test_audit_request_requires_start_decision() -> None:
    """Do not create a physical audit request when human handling is required."""
    payload = _payload("audit-request", "single-dispute.json")
    payload["gate"]["neutral_packet_valid"] = False  # type: ignore[index]
    payload["gate"]["decision"] = "human_decision_required"  # type: ignore[index]

    with pytest.raises(ValidationError, match="requires a start_audit"):
        AuditRequestV1.model_validate(payload)


@pytest.mark.parametrize(
    ("opinion", "next_state"),
    [
        ("support_interpretation_1", "primary_review"),
        ("support_interpretation_2", "primary_review"),
        ("support_neither", "primary_review"),
        ("indeterminate_more_evidence_possible", "research"),
        ("indeterminate_no_more_evidence", "human_decision"),
        ("human_judgment_required", "human_decision"),
    ],
)
def test_valid_audit_opinion_maps_to_approved_next_state(opinion: str, next_state: str) -> None:
    """Apply every valid audit opinion through the approved decision table."""
    payload = _payload("audit-result", "support-first.json")
    payload["opinion"] = opinion
    payload["recommended_next_state"] = next_state

    result = AuditResultV1.model_validate(payload)

    assert result.recommended_next_state.value == next_state


def test_failed_audit_cannot_contain_an_adopted_opinion() -> None:
    """Keep execution failures separate from audit opinions and logical counts."""
    payload = _payload("audit-result", "support-first.json")
    payload["execution_result"] = "execution_failed"
    payload["schema_valid"] = False
    payload["logical_audit_counted"] = False
    payload["recommended_next_state"] = "failed"

    with pytest.raises(ValidationError, match="must not contain an adopted audit opinion"):
        AuditResultV1.model_validate(payload)


def test_a_dispute_can_have_only_one_valid_logical_audit() -> None:
    """Count valid indeterminate and decisive results under the same one-audit rule."""
    first = AuditResultV1.model_validate(_payload("audit-result", "support-first.json"))
    second_payload = copy.deepcopy(_payload("audit-result", "support-first.json"))
    second_payload["artifact_id"] = "audit-result-2"
    second_payload["audit_id"] = "audit-2"
    second_payload["opinion"] = "indeterminate_no_more_evidence"
    second_payload["recommended_next_state"] = "human_decision"
    second = AuditResultV1.model_validate(second_payload)

    with pytest.raises(ValueError, match="at most one valid logical audit"):
        validate_logical_audit_history([first, second])


def test_human_decision_must_select_a_recorded_option() -> None:
    """Keep the human workflow decision bounded by the presented options."""
    payload = _payload("human-decision", "workflow-direction.json")
    payload["selected_option"] = "buy"

    with pytest.raises(ValidationError, match="one of the recorded allowed_options"):
        HumanDecisionV1.model_validate(payload)
