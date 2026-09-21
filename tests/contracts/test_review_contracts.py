"""Tests for primary review, finding, response, and re-review contracts."""

import json
import typing
from pathlib import Path

import pytest
from pydantic import ValidationError

from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.review import (
    MaterialityAssessmentV1,
    PrimaryReviewV1,
    ReviewResponseV1,
)
from stock_research_llm_orchestrator.contracts.validation.wrapper import InputFormat, validate_text


FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "contracts" / "detailed-analysis" / "v1"


def _payload(artifact: str, filename: str) -> dict[str, typing.Any]:
    path = FIXTURE_ROOT / artifact / "valid" / filename
    parsed = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict)
    assert all(isinstance(key, str) for key in parsed)
    return typing.cast("dict[str, typing.Any]", parsed)


def _reference(schema_id: str, artifact_id: str) -> dict[str, typing.Any]:
    return {
        "artifact_type": schema_id.removeprefix("detailed-analysis."),
        "artifact_id": artifact_id,
        "schema_id": schema_id,
        "schema_version": 1,
        "sha256": "9" * 64,
    }


def _finding(*, pending: bool, severity: str, lifecycle: str) -> dict[str, typing.Any]:
    return {
        "finding_id": "finding-1",
        "target_artifact_reference": _reference("detailed-analysis.synthesis-result", "synthesis-1"),
        "target_claim_ids": ["claim-1"],
        "json_pointer": "/horizon_results/0/assessment",
        "horizon": "medium_term",
        "summary": "The assessment requires review.",
        "detail": "The policy interpretation can change the horizon assessment.",
        "primary_classification": None if pending else "policy_application_difference",
        "secondary_classifications": [],
        "classification_reason": None if pending else "Two policy readings remain possible.",
        "classification_pending": {
            "reason": "The affected rule must be identified.",
            "candidates": [
                {
                    "classification": "policy_application_difference",
                    "rejected_reason": "The policy rule has not been isolated.",
                },
                {
                    "classification": "logical_gap",
                    "rejected_reason": "The reasoning structure has not been rechecked.",
                },
            ],
            "next_classifier": "primary_reviewer",
        }
        if pending
        else None,
        "severity": severity,
        "severity_reason": "The medium-term assessment could change.",
        "evidence_ids": ["evidence-1"],
        "policy_references": [_reference("detailed-analysis.detailed-analysis-policy", "policy-1")],
        "schema_references": ["detailed-analysis.synthesis-result:v1"],
        "materiality": {"impacts": [], "is_material": False},
        "expected_resolution": "Reapply the cited policy rule.",
        "lifecycle": lifecycle,
        "unresolved_retention_reason": None,
    }


@pytest.mark.parametrize(
    ("artifact", "filename", "model"),
    [
        ("primary-review", "approved.json", PrimaryReviewV1),
        ("review-response", "accepted.json", ReviewResponseV1),
    ],
)
def test_valid_review_fixtures(artifact: str, filename: str, model: type[object]) -> None:
    """Dispatch both public review artifacts through the common validator."""
    path = FIXTURE_ROOT / artifact / "valid" / filename

    result = validate_text(path.read_text(encoding="utf-8"), InputFormat.JSON)

    assert isinstance(result, model)


def test_classification_pending_blocks_review_pass() -> None:
    """Do not allow a provisional classification to be hidden by an approve status."""
    payload = _payload("primary-review", "approved.json")
    payload["findings"] = [_finding(pending=True, severity="high", lifecycle="open")]

    with pytest.raises(ValidationError, match="review_passed must equal"):
        PrimaryReviewV1.model_validate(payload)


def test_materiality_flag_is_derived_from_valid_approved_impacts() -> None:
    """Reject a caller-provided materiality value that disagrees with the impacts."""
    payload = {
        "impacts": [
            {
                "impact_id": "impact-1",
                "impact_type": "major_risk",
                "target_id": "risk-1",
                "horizon": "long_term",
                "interpretation_1_effect": "major",
                "interpretation_2_effect": "minor",
                "reason": "The risk prominence changes.",
                "valid": True,
            }
        ],
        "is_material": False,
    }

    with pytest.raises(ValidationError, match="is_material must be derived"):
        MaterialityAssessmentV1.model_validate(payload)


def test_retained_medium_finding_requires_reason() -> None:
    """Require an explicit reason before leaving a non-major finding unresolved."""
    payload = _payload("primary-review", "approved.json")
    payload["overall_status"] = "approve_with_changes"
    payload["review_passed"] = False
    payload["findings"] = [_finding(pending=False, severity="medium", lifecycle="open")]

    with pytest.raises(ValidationError, match="unresolved retention reason"):
        PrimaryReviewV1.model_validate(payload)


def test_orchestrator_rejection_cannot_resolve_a_major_finding() -> None:
    """Keep high and critical findings open for reviewer, audit, or human handling."""
    payload = _payload("review-response", "accepted.json")
    payload["finding_severity"] = "critical"
    payload["disposition"] = "rejected"
    payload["proposed_lifecycle"] = "resolved"

    with pytest.raises(ValidationError, match="must be disputed or sent to human decision"):
        ReviewResponseV1.model_validate(payload)


def test_primary_reviewer_can_confirm_major_finding_resolution() -> None:
    """Record the reviewer confirmation in a new primary-review artifact."""
    payload = _payload("primary-review", "approved.json")
    response_ref = _reference("detailed-analysis.review-response", "review-response-artifact-1")
    payload["previous_review_reference"] = _reference("detailed-analysis.primary-review", "primary-review-1")
    payload["reviewed_response_references"] = [response_ref]
    payload["review_round"] = 2
    payload["findings"] = [_finding(pending=False, severity="high", lifecycle="resolved")]
    payload["reviewer_rechecks"] = [
        {
            "recheck_id": "recheck-1",
            "finding_id": "finding-1",
            "response_reference": response_ref,
            "reviewer_agent_run_id": "reviewer-run-2",
            "created_at": "2026-09-12T10:10:00+09:00",
            "decision": "accepted",
            "rationale": "The revised claim now satisfies the policy and evidence requirements.",
            "resulting_severity": "high",
            "resulting_lifecycle": "resolved",
        }
    ]

    result = PrimaryReviewV1.model_validate(payload)

    assert result.review_passed is True
