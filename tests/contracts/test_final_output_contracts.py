"""Tests for final-analysis and instruction-application contracts."""

import json
import typing
from pathlib import Path

import pytest
from pydantic import ValidationError

from stock_research_llm_orchestrator.contracts.base import FileReferenceV1
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.final_output import FinalAnalysisResultV1
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.instructions import InstructionApplicationV1
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
        ("final-analysis-result", "completed.json", FinalAnalysisResultV1),
        ("instruction-application", "codex-worker.json", InstructionApplicationV1),
    ],
)
def test_valid_final_output_fixtures(artifact: str, filename: str, model: type[object]) -> None:
    """Dispatch both public final-output artifacts through the common validator."""
    path = FIXTURE_ROOT / artifact / "valid" / filename

    result = validate_text(path.read_text(encoding="utf-8"), InputFormat.JSON)

    assert isinstance(result, model)


def test_final_result_preserves_horizon_order_without_combined_rating() -> None:
    """Keep medium- and long-term assessments separate in the final result."""
    payload = _payload("final-analysis-result", "completed.json")
    payload["horizon_results"] = list(reversed(payload["horizon_results"]))  # type: ignore[arg-type]

    with pytest.raises(ValidationError, match="medium_term followed by long_term"):
        FinalAnalysisResultV1.model_validate(payload)


def test_final_report_uses_explicit_language_override() -> None:
    """Apply only an explicit task-level override to the human-facing report."""
    payload = _payload("final-analysis-result", "completed.json")
    payload["human_document_language_override"] = {
        "target_documents": ["final_report"],
        "language": "en",
        "reason": "The designated human reviewer requested English.",
    }

    with pytest.raises(ValidationError, match="must match the task override"):
        FinalAnalysisResultV1.model_validate(payload)


def test_file_reference_rejects_parent_traversal() -> None:
    """Keep stored file references inside the task or repository artifact tree."""
    payload = {
        "artifact_id": "file-1",
        "relative_path": "../secrets.txt",
        "media_type": "text/plain",
        "sha256": "0" * 64,
    }

    with pytest.raises(ValidationError, match="must not traverse parents"):
        FileReferenceV1.model_validate(payload)


def test_instruction_composition_requires_common_then_role() -> None:
    """Reject an instruction set that applies a role before common safety rules."""
    payload = _payload("instruction-application", "codex-worker.json")
    payload["components"] = list(reversed(payload["components"]))  # type: ignore[arg-type]
    for order, component in enumerate(payload["components"], start=1):  # type: ignore[union-attr]
        component["order"] = order

    with pytest.raises(ValidationError, match="common, role, then task_scoped"):
        InstructionApplicationV1.model_validate(payload)


def test_instruction_application_cannot_trust_external_content() -> None:
    """Keep fetched content in the untrusted data boundary."""
    payload = _payload("instruction-application", "codex-worker.json")
    payload["external_content_treated_as_untrusted_data"] = False

    with pytest.raises(ValidationError):
        InstructionApplicationV1.model_validate(payload)
