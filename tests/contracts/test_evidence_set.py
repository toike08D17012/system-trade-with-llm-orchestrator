"""Tests for version 1 evidence provenance and lifecycle contracts."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.evidence import (
    EvidenceLayer,
    EvidenceSetV1,
)
from stock_research_llm_orchestrator.contracts.validation.wrapper import InputFormat, validate_text


FIXTURE_PATH = (
    Path(__file__).parents[1]
    / "fixtures"
    / "contracts"
    / "detailed-analysis"
    / "v1"
    / "evidence-set"
    / "valid"
    / "frozen.json"
)


def _payload() -> dict[str, object]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_frozen_evidence_set_preserves_four_layers_and_provenance() -> None:
    """Accept distinct metadata, raw, normalized, and calculated records."""
    artifact = validate_text(FIXTURE_PATH.read_text(encoding="utf-8"), InputFormat.JSON)

    assert isinstance(artifact, EvidenceSetV1)
    assert tuple(record.layer for record in artifact.records) == (
        EvidenceLayer.SOURCE_METADATA,
        EvidenceLayer.RAW,
        EvidenceLayer.NORMALIZED,
        EvidenceLayer.CALCULATED,
    )


def test_evidence_set_rejects_unknown_provenance_reference() -> None:
    """Require every derived input to exist in the same immutable set."""
    payload = _payload()
    payload["records"][2]["input_evidence_ids"] = ["missing-evidence"]  # type: ignore[index]

    with pytest.raises(ValidationError, match="input references must exist"):
        EvidenceSetV1.model_validate(payload)


def test_evidence_set_rejects_lifecycle_time_reversal() -> None:
    """Require freshness evaluation after evidence freezing."""
    payload = _payload()
    payload["freshness_checked_at"] = "2026-09-11T10:29:00+09:00"

    with pytest.raises(ValidationError, match="must not precede"):
        EvidenceSetV1.model_validate(payload)


def test_missing_derived_value_requires_reason_instead_of_fill() -> None:
    """Keep missing data explicit rather than filling it with zero."""
    payload = _payload()
    payload["records"][2]["value"] = None  # type: ignore[index]

    with pytest.raises(ValidationError, match="missing_reason is required"):
        EvidenceSetV1.model_validate(payload)


def test_invalidated_evidence_set_names_affected_artifacts() -> None:
    """Record the material update and exact downstream artifacts to rerun."""
    payload = _payload()
    payload["state"] = "invalidated"
    payload["invalidations"] = [
        {
            "invalidation_id": "invalidation-001",
            "created_at": "2026-09-11T11:00:00+09:00",
            "triggering_evidence_ids": ["normalized-001"],
            "affected_artifact_ids": ["worker-analysis-codex-v1"],
            "reason": "A material source correction changed the normalized value.",
        }
    ]

    artifact = EvidenceSetV1.model_validate(payload)

    assert artifact.invalidations[0].affected_artifact_ids == ("worker-analysis-codex-v1",)
