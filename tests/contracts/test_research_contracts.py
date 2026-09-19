"""Tests for independent research and common-evidence update contracts."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.research import (
    CandidateValidationStatus,
    CommonEvidenceUpdateV1,
    ResearchContextV1,
    SearchRecordV1,
)
from stock_research_llm_orchestrator.contracts.validation.wrapper import InputFormat, validate_text


FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "contracts" / "detailed-analysis" / "v1"


def _fixture(artifact_name: str, case_name: str) -> str:
    return (FIXTURE_ROOT / artifact_name / "valid" / case_name).read_text(encoding="utf-8")


def test_research_context_contains_only_common_inputs_for_one_worker() -> None:
    """Provide exact shared evidence and policies without a peer conclusion field."""
    text = _fixture("research-context", "codex-worker.json")
    artifact = validate_text(text, InputFormat.JSON)

    assert isinstance(artifact, ResearchContextV1)
    assert artifact.recipient_role == "codex_worker"
    assert set(artifact.source_metadata_evidence_ids).issubset(artifact.common_evidence_ids)
    assert "provisional_conclusion" not in type(artifact).model_fields


def test_research_context_rejects_peer_provisional_conclusion() -> None:
    """Forbid leaking a worker conclusion through an unknown context field."""
    payload = json.loads(_fixture("research-context", "codex-worker.json"))
    payload["peer_provisional_conclusion"] = "buy"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ResearchContextV1.model_validate(payload)


def test_search_record_keeps_candidate_separate_from_verified_evidence() -> None:
    """Accept evidence IDs only after an original document was validated."""
    artifact = validate_text(_fixture("search-record", "accepted-candidate.json"), InputFormat.JSON)

    assert isinstance(artifact, SearchRecordV1)
    assert artifact.results[0].validation.status is CandidateValidationStatus.ACCEPTED
    assert artifact.results[0].validation.resulting_evidence_ids == ("source-meta-002", "raw-002")


def test_unverified_candidate_cannot_create_evidence() -> None:
    """Reject direct promotion of an unverified search result to evidence."""
    payload = json.loads(_fixture("search-record", "accepted-candidate.json"))
    validation = payload["results"][0]["validation"]
    validation["status"] = "unverified"
    validation["verified_at"] = None
    validation["verifier_role"] = None

    with pytest.raises(ValidationError, match="must not claim verification or evidence"):
        SearchRecordV1.model_validate(payload)


def test_common_evidence_update_distributes_same_union_to_both_workers() -> None:
    """Preserve discovery origin while advancing the shared set exactly once."""
    artifact = validate_text(_fixture("common-evidence-update", "agent-discovery.json"), InputFormat.JSON)

    assert isinstance(artifact, CommonEvidenceUpdateV1)
    assert artifact.distributed_to == ("codex_worker", "claude_worker")
    assert {item.evidence_id for item in artifact.discoveries} == set(artifact.added_evidence_ids)


def test_common_evidence_update_rejects_one_worker_distribution() -> None:
    """Require verified additions to return to both independent workers."""
    payload = json.loads(_fixture("common-evidence-update", "agent-discovery.json"))
    payload["distributed_to"] = ["codex_worker"]

    with pytest.raises(ValidationError):
        CommonEvidenceUpdateV1.model_validate(payload)
