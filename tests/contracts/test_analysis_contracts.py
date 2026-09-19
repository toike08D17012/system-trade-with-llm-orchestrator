"""Tests for worker analysis and orchestrator synthesis contracts."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.analysis import (
    AssessmentStatus,
    SynthesisResultV1,
    WorkerAnalysisV1,
)
from stock_research_llm_orchestrator.contracts.validation.wrapper import InputFormat, validate_text


FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "contracts" / "detailed-analysis" / "v1"


def _fixture(artifact: str, case: str) -> str:
    return (FIXTURE_ROOT / artifact / "valid" / case).read_text(encoding="utf-8")


def test_worker_analysis_separates_horizon_assessments_and_claim_types() -> None:
    """Accept five-perspective medium- and long-term analyses in stable order."""
    artifact = validate_text(_fixture("worker-analysis", "codex-worker.json"), InputFormat.JSON)

    assert isinstance(artifact, WorkerAnalysisV1)
    assert artifact.horizon_analyses[0].assessment is not None
    assert artifact.horizon_analyses[0].assessment.status is AssessmentStatus.CONDITIONAL
    assert artifact.horizon_analyses[1].assessment is not None
    assert artifact.horizon_analyses[1].assessment.status is AssessmentStatus.INVESTIGATE


def test_fact_claim_without_evidence_is_rejected() -> None:
    """Prevent an unsupported factual statement from entering analysis."""
    payload = json.loads(_fixture("worker-analysis", "codex-worker.json"))
    payload["horizon_analyses"][0]["claims"][0]["evidence_ids"] = []

    with pytest.raises(ValidationError, match="fact claims require"):
        WorkerAnalysisV1.model_validate(payload)


def test_not_evaluable_horizon_cannot_have_four_level_assessment() -> None:
    """Keep evaluability separate from the four-level result."""
    payload = json.loads(_fixture("worker-analysis", "codex-worker.json"))
    payload["horizon_analyses"][1]["evaluability"] = "not_evaluable"

    with pytest.raises(ValidationError, match="must not contain an assessment"):
        WorkerAnalysisV1.model_validate(payload)


def test_synthesis_preserves_both_workers_and_unresolved_difference() -> None:
    """Accept two-family synthesis without voting or averaging away disagreement."""
    artifact = validate_text(_fixture("synthesis-result", "two-worker.json"), InputFormat.JSON)

    assert isinstance(artifact, SynthesisResultV1)
    assert artifact.differences[0].resolution_status == "research_required"
    assert artifact.differences_preserved_without_voting_or_averaging is True


def test_synthesis_rejects_missing_worker_family() -> None:
    """Require one Codex and one Claude independent analysis."""
    payload = json.loads(_fixture("synthesis-result", "two-worker.json"))
    payload["worker_inputs"][1]["worker_role"] = "codex_worker"

    with pytest.raises(ValidationError, match="requires Codex then Claude"):
        SynthesisResultV1.model_validate(payload)


def test_cross_horizon_summary_rejects_combined_rating_field() -> None:
    """Forbid a third four-level rating across the two horizons."""
    payload = json.loads(_fixture("synthesis-result", "two-worker.json"))
    payload["cross_horizon_summary"]["assessment"] = "pass"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        SynthesisResultV1.model_validate(payload)
