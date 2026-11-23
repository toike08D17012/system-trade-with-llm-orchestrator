"""Preserve original evidence and require explicit specialist adoption decisions."""

import json
from copy import deepcopy
from typing import cast

import pytest
from pydantic import JsonValue, ValidationError

from stock_research_llm_orchestrator.agents.configuration import load_agent_configuration
from stock_research_llm_orchestrator.agents.research_provider import WebTurnResult
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.acquisition_analysis import AcquisitionAnalysisV1
from stock_research_llm_orchestrator.research.acquisition import run_workflow, validate_bundle
from stock_research_llm_orchestrator.research.incremental import (
    OptimizedSpecialistTurn,
    changed_evidence_ids,
    compact_request,
    resolve_unchanged,
)
from stock_research_llm_orchestrator.research.workflow import ResearchRequest

from .test_acquisition import INSTRUCTIONS, _acquire, _acquisition_turn, _final, _request
from .test_configured import PROFILE
from .test_workflow import _inputs


def test_delta_ignores_receipt_alias_but_includes_validation_and_new_counterevidence() -> None:
    """New originals matter even when every previously cited original remains unchanged."""
    item = _acquire(_request(), "track_a_capital", 0)[0].evidence[0]
    receipt = item.model_copy(
        update={
            "retrieved_at": "2026-11-15T00:00:00+00:00",
            "raw_reference": item.raw_reference.model_copy(update={"relative_path": "another/filing.txt"}),
        }
    )
    assert changed_evidence_ids({item.evidence_id: item}, {item.evidence_id: receipt}) == []
    rejected = item.model_copy(update={"validation": "rejected"})
    assert changed_evidence_ids({item.evidence_id: item}, {item.evidence_id: rejected}) == [item.evidence_id]
    counterevidence = item.model_copy(
        update={"evidence_id": "new-counterevidence", "text": "Profit forecast withdrawn."}
    )
    assert changed_evidence_ids(
        {item.evidence_id: item}, {item.evidence_id: item, counterevidence.evidence_id: counterevidence}
    ) == [counterevidence.evidence_id]
    assert changed_evidence_ids({item.evidence_id: item}, {}) == [item.evidence_id]


@pytest.mark.parametrize(
    "changes",
    [
        {"state": " "},
        {"requests": [_request().model_dump(mode="json")]},
        {"kind": "final"},
        {"kind": "acquire"},
    ],
)
def test_unchanged_requires_unambiguous_explicit_rationale(changes: dict[str, JsonValue]) -> None:
    """A blank decision cannot silently bypass an entire evidence assessment."""
    body: dict[str, JsonValue] = {
        "kind": "unchanged",
        "requests": [],
        "state": "All changed originals corroborate prior assumptions.",
        "payload": None,
    }
    with pytest.raises(ValidationError):
        OptimizedSpecialistTurn.model_validate_json(json.dumps({**body, **changes}))


def test_unchanged_adopts_only_the_exact_previous_specialist_payload() -> None:
    """Adoption is unavailable on the first pass and cannot fabricate a replacement analysis."""
    body: dict[str, JsonValue] = {
        "kind": "unchanged",
        "requests": [],
        "state": "New evidence corroborates the existing scenario assumptions.",
        "payload": None,
    }
    with pytest.raises(ValueError, match="incremental_previous_specialist_required"):
        resolve_unchanged(body, None)
    files = run_workflow(
        _inputs(), load_agent_configuration(PROFILE), _final, INSTRUCTIONS, mode="replay", acquire=_acquire
    )
    previous = AcquisitionAnalysisV1.model_validate_json(files["track_a_capital.json"])
    assert resolve_unchanged(body, previous) == previous.payload.model_dump(mode="json")
    review = AcquisitionAnalysisV1.model_validate_json(files["primary_reviewer.json"])
    with pytest.raises(ValueError, match="incremental_previous_specialist_required"):
        resolve_unchanged(body, review)


def test_compaction_removes_only_duplicate_sources_and_continuation_outputs() -> None:
    """Unmatched sources and complete passages remain available without modifying saved data."""
    source: dict[str, JsonValue] = {"source_id": "track_a_capital-web-source", "quote": "A complete original passage."}
    unique: dict[str, JsonValue] = {"source_id": "other", "quote": "Unique counterevidence."}
    data: dict[str, JsonValue] = {
        "specialists": {"track_a_capital": {"payload": {"research": {"sources": [source]}}}},
        "previous_web_sources": {"track_a_capital-web-source": source, "other": unique},
        "own_previous_turns": [{"kind": "acquire", "state": "Copied old state"}],
        "continuation_state": "Current state",
        "evidence_reads": [{"selected_lines": ["Complete text."]}],
    }
    original = deepcopy(data)
    compact = compact_request(data)
    assert data == original
    assert compact["own_previous_turns"] == []
    assert compact["continuation_state"] == "Current state"
    assert compact["evidence_reads"] == original["evidence_reads"]
    sources = cast(dict[str, JsonValue], compact["previous_web_sources"])
    assert sources["track_a_capital-web-source"] == {"retained_in_dependency": "track_a_capital"}
    assert sources["other"] == unique
    assert compact["specialists"] == original["specialists"]


def test_material_delta_revises_analysis_and_reconstruction_rejects_tampered_binding() -> None:
    """A new uncited original is assessed and a revised payload remains bound to that delta."""
    asked = False

    def invoke(request: ResearchRequest) -> WebTurnResult:
        nonlocal asked
        if request.stage == "track_a_capital" and not asked:
            asked = True
            return _acquisition_turn()
        result = _final(request)
        if request.stage == "track_a_capital" and request.data.get("evidence_epoch") == 2:
            assert request.data["new_common_evidence_ids"] == ["acquired-filing"]
            previous = cast(dict[str, JsonValue], request.data["own_previous_analysis"])
            assert "acquired-filing" not in json.dumps(previous["payload"])
            body = json.loads(result.body)
            body["state"] = "The new original contradicts an uncited assumption, requiring a revised thesis."
            body["payload"]["horizons"][0]["rationale"] = "Revised after assessing the new original."
            return WebTurnResult(json.dumps(body).encode(), b"")
        return result

    files = run_workflow(
        _inputs(),
        load_agent_configuration(PROFILE),
        invoke,
        INSTRUCTIONS,
        mode="replay",
        acquire=_acquire,
        execution_profile="parallel_incremental_v1",
        reassessment_from_epoch=2,
        stable_evidence_from_epoch=1,
        batch_common_updates_from_epoch=1,
    )
    final = json.loads(files["track_a_capital.json"])
    assert final["payload"]["horizons"][0]["rationale"] == "Revised after assessing the new original."
    decisions = json.loads(files["incremental-decisions.json"])
    assert decisions[0]["decision"] == "revised"
    altered = dict(files)
    decisions[0]["previous_artifact_sha256"] = "0" * 64
    altered["incremental-decisions.json"] = json.dumps(decisions).encode()
    with pytest.raises(ValueError, match="acquisition_bundle_binding_invalid"):
        validate_bundle(altered)
