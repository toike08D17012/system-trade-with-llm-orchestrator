"""Boundary tests for standard acquisition results and resumable role requests."""

import json
from pathlib import Path
from typing import cast

import pytest
from pydantic import ValidationError

from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.acquisition_analysis import (
    AcquisitionRequest,
    AcquisitionResult,
    AcquisitionSnapshotV1,
    AcquisitionSpecialistTurn,
    EvidenceReadRequest,
)


FIXTURES = Path(__file__).parents[1] / "fixtures/contracts/detailed-analysis/v1"


def _result() -> dict[str, object]:
    return cast(dict[str, object], json.loads((FIXTURES / "acquisition-result/valid/minimum.json").read_text()))


def test_acquired_result_requires_recorded_physical_attempt_or_reuse() -> None:
    """An original cannot be described as acquired without acquisition provenance."""
    body = _result()
    body["attempt_references"] = []
    with pytest.raises(ValidationError, match="acquisition_original_or_attempt_missing"):
        AcquisitionResult.model_validate(body)


def test_failed_result_cannot_include_evidence() -> None:
    """Unsuccessful acquisition must never register a source as acquired."""
    body = _result()
    body["status"] = "failed"
    body["validation"] = "pending"
    with pytest.raises(ValidationError, match="acquisition_unavailable_evidence_invalid"):
        AcquisitionResult.model_validate(body)


def test_acquired_result_requires_registered_original_hash() -> None:
    """The evidence original must match a persisted result file exactly."""
    body = _result()
    body["files"] = [
        {
            "artifact_id": "wrong-original",
            "relative_path": "acquisition/other.txt",
            "media_type": "text/plain",
            "sha256": "f" * 64,
        }
    ]
    with pytest.raises(ValidationError, match="acquisition_original_not_registered"):
        AcquisitionResult.model_validate(body)


@pytest.mark.parametrize("url", ["file:///etc/passwd", "https://user:password@example.com", "https://example.com/\n"])
def test_document_request_rejects_unsafe_url_shapes(url: str) -> None:
    """Only credential-free HTTP references may enter the public fetch boundary."""
    with pytest.raises(ValidationError, match="acquisition_document_url_invalid"):
        AcquisitionRequest(
            operation="public_document",
            security_code="7203",
            purpose="Read disclosure",
            url=url,
            classification="issuer-ir",
        )


def test_market_request_rejects_inverted_period() -> None:
    """Malformed requested periods fail before acquisition starts."""
    with pytest.raises(ValidationError, match="acquisition_market_period_invalid"):
        AcquisitionRequest.model_validate_json(
            json.dumps(
                {
                    "operation": "market",
                    "security_code": "7203",
                    "purpose": "Get price history",
                    "start": "2026-11-14",
                    "end": "2026-11-13",
                }
            )
        )


def test_acquisition_turn_cannot_claim_final_analysis_without_payload() -> None:
    """A final marker alone cannot complete the role."""
    with pytest.raises(ValidationError, match="acquisition_turn_final_payload_required"):
        AcquisitionSpecialistTurn(kind="final", requests=(), payload=None)


def test_snapshot_rejects_duplicate_source_identity() -> None:
    """An evidence identity may occur only once in a visible snapshot."""
    body = json.loads((FIXTURES / "acquisition-snapshot/valid/minimum.json").read_text())
    body["evidence"] *= 2
    with pytest.raises(ValidationError, match="acquisition_snapshot_duplicate_evidence"):
        AcquisitionSnapshotV1.model_validate(body)


def test_verified_result_cannot_contain_pending_evidence() -> None:
    """Aggregate verification cannot conceal unresolved source validation."""
    body = _result()
    evidence = body["evidence"]
    assert isinstance(evidence, list)
    evidence[0]["validation"] = "pending"
    with pytest.raises(ValidationError, match="acquisition_aggregate_validation_mismatch"):
        AcquisitionResult.model_validate(body)


@pytest.mark.parametrize(
    "payload",
    [
        {"operation": "search_evidence", "evidence_id": "issuer-ir-1", "query": "Dividend coverage"},
        {"operation": "read_evidence", "evidence_id": "issuer-ir-1", "start_line": 1, "end_line": 25},
    ],
)
def test_retained_evidence_requests_accept_search_or_inclusive_line_ranges(payload: dict[str, object]) -> None:
    """A role can inspect retained sources without triggering another public acquisition."""
    request = EvidenceReadRequest.model_validate(payload)
    assert request.evidence_id == "issuer-ir-1"
    assert request.operation == payload["operation"]


@pytest.mark.parametrize(
    "fields",
    [
        {"operation": "search_evidence", "query": None},
        {"operation": "search_evidence", "query": ""},
        {"operation": "search_evidence", "query": " \n "},
        {"operation": "search_evidence", "query": "Dividend", "start_line": 1},
        {"operation": "search_evidence", "query": "Dividend", "end_line": 5},
        {"operation": "read_evidence", "start_line": None, "end_line": 5},
        {"operation": "read_evidence", "start_line": 1, "end_line": None},
        {"operation": "read_evidence", "start_line": 0, "end_line": 5},
        {"operation": "read_evidence", "start_line": True, "end_line": 5},
        {"operation": "read_evidence", "start_line": 6, "end_line": 5},
        {"operation": "read_evidence", "start_line": 1, "end_line": 5, "query": "Dividend"},
    ],
)
def test_retained_evidence_requests_reject_ambiguous_or_invalid_selection(fields: dict[str, object]) -> None:
    """Search cannot silently imply a range, and reading cannot silently imply a query."""
    with pytest.raises(ValidationError):
        EvidenceReadRequest.model_validate({"evidence_id": "issuer-ir-1", **fields})


def test_role_turn_preserves_mixed_acquisition_and_retained_read_requests() -> None:
    """A role can acquire another source and inspect existing evidence in one continuation turn."""
    turn = AcquisitionSpecialistTurn.model_validate_json(
        json.dumps(
            {
                "kind": "acquire",
                "requests": [
                    {"operation": "listing", "security_code": "7203", "purpose": "Verify security identity"},
                    {"operation": "search_evidence", "evidence_id": "issuer-ir-1", "query": "Dividend"},
                    {"operation": "read_evidence", "evidence_id": "issuer-ir-1", "start_line": 7, "end_line": 12},
                ],
                "state": "Read original dividend policy before concluding.",
                "payload": None,
            }
        )
    )
    assert isinstance(turn.requests[0], AcquisitionRequest)
    assert isinstance(turn.requests[1], EvidenceReadRequest)
    assert isinstance(turn.requests[2], EvidenceReadRequest)
    assert turn.requests[2].end_line == 12
