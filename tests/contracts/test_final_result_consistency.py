"""Tests for final-result cross-artifact consistency."""

import copy
import json
import typing
from pathlib import Path

import pytest

from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.analysis import SynthesisResultV1
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.evidence import EvidenceSetV1
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.final_output import FinalAnalysisResultV1
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.review import PrimaryReviewV1
from stock_research_llm_orchestrator.contracts.errors import ErrorCode
from stock_research_llm_orchestrator.contracts.validation.final_output import validate_final_result_consistency
from stock_research_llm_orchestrator.contracts.validation.issues import RuleValidationError


FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "contracts" / "detailed-analysis" / "v1"


def _payload(artifact: str, filename: str) -> dict[str, typing.Any]:
    path = FIXTURE_ROOT / artifact / "valid" / filename
    parsed = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict)
    assert all(isinstance(key, str) for key in parsed)
    return typing.cast("dict[str, typing.Any]", parsed)


def _aligned_artifacts() -> tuple[FinalAnalysisResultV1, SynthesisResultV1, PrimaryReviewV1, EvidenceSetV1]:
    final_payload = _payload("final-analysis-result", "completed.json")
    synthesis_payload = _payload("synthesis-result", "two-worker.json")
    review_payload = _payload("primary-review", "approved.json")
    evidence_payload = _payload("evidence-set", "frozen.json")

    synthesis_payload["task_id"] = "task-1"
    synthesis_payload["artifact_id"] = "synthesis-1"
    synthesis_payload["evidence_set_reference"] = copy.deepcopy(final_payload["evidence_set_reference"])
    synthesis_payload["evidence_set_reference"]["artifact_id"] = "evidence-set-v1"  # type: ignore[index]
    synthesis_payload["evidence_set_version"] = 1
    synthesis_payload["horizon_results"] = copy.deepcopy(final_payload["horizon_results"])
    synthesis_payload["cross_horizon_summary"] = copy.deepcopy(final_payload["cross_horizon_summary"])
    synthesis_payload["entry_exit_context"] = copy.deepcopy(final_payload["entry_exit_context"])
    synthesis_payload["entry_exit_context"]["evidence_ids"] = ["normalized-001", "calculated-001"]  # type: ignore[index]
    synthesis_payload["differences"] = []

    final_payload["evidence_set_reference"]["artifact_id"] = "evidence-set-v1"  # type: ignore[index]
    final_payload["evidence_set_version"] = 1
    final_payload["horizon_results"] = copy.deepcopy(synthesis_payload["horizon_results"])
    final_payload["cross_horizon_summary"] = copy.deepcopy(synthesis_payload["cross_horizon_summary"])
    final_payload["entry_exit_context"] = copy.deepcopy(synthesis_payload["entry_exit_context"])

    evidence_payload["task_id"] = "task-1"
    review_payload["evidence_set_version"] = 1

    return (
        FinalAnalysisResultV1.model_validate(final_payload),
        SynthesisResultV1.model_validate(synthesis_payload),
        PrimaryReviewV1.model_validate(review_payload),
        EvidenceSetV1.model_validate(evidence_payload),
    )


def test_final_result_matches_reviewed_synthesis_and_evidence() -> None:
    """Accept an exact final projection whose references resolve."""
    validate_final_result_consistency(*_aligned_artifacts())


def test_final_result_rejects_unknown_evidence_reference() -> None:
    """Reject evidence IDs absent from the adopted evidence set."""
    final_result, synthesis, primary_review, evidence_set = _aligned_artifacts()
    payload = final_result.model_dump(mode="json")
    payload["entry_exit_context"]["evidence_ids"] = ["missing-evidence"]
    changed_final = FinalAnalysisResultV1.model_validate(payload)
    synthesis_payload = synthesis.model_dump(mode="json")
    synthesis_payload["entry_exit_context"] = copy.deepcopy(payload["entry_exit_context"])
    changed_synthesis = SynthesisResultV1.model_validate(synthesis_payload)

    with pytest.raises(RuleValidationError) as captured:
        validate_final_result_consistency(changed_final, changed_synthesis, primary_review, evidence_set)

    assert captured.value.issue.code is ErrorCode.REFERENCE_NOT_FOUND


def test_final_result_rejects_changed_assessment() -> None:
    """Reject a final assessment that differs from the reviewed synthesis."""
    final_result, synthesis, primary_review, evidence_set = _aligned_artifacts()
    payload = final_result.model_dump(mode="json")
    payload["horizon_results"][0]["assessment"]["status"] = "pass"
    changed_final = FinalAnalysisResultV1.model_validate(payload)

    with pytest.raises(RuleValidationError) as captured:
        validate_final_result_consistency(changed_final, synthesis, primary_review, evidence_set)

    assert captured.value.issue.code is ErrorCode.ARTIFACT_CONFLICT
