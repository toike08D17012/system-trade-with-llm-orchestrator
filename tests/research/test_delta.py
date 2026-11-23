"""Verify hash-bound partial revisions and complete typed payload reconstruction."""

import json
from hashlib import sha256
from typing import cast

import pytest
from pydantic import JsonValue, ValidationError

from stock_research_llm_orchestrator.agents.configuration import load_agent_configuration
from stock_research_llm_orchestrator.agents.research_provider import _codex_output_schema
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.acquisition_analysis import AcquisitionAnalysisV1
from stock_research_llm_orchestrator.research.acquisition import run_workflow
from stock_research_llm_orchestrator.research.delta import (
    DeltaAggregateTurn,
    DeltaMainTurn,
    DeltaReviewTurn,
    DeltaSpecialistTurn,
    _pointer_parts,
    resolve_adoption,
    resolve_delta,
)
from stock_research_llm_orchestrator.research.input import serialize

from .test_acquisition import INSTRUCTIONS, RESEARCH_ROLES, _acquire, _final
from .test_configured import PROFILE
from .test_workflow import _inputs


@pytest.fixture(scope="module")
def previous_artifacts() -> dict[str, AcquisitionAnalysisV1]:
    """Use real validated role artifacts without provider or network access."""
    files = run_workflow(
        _inputs(), load_agent_configuration(PROFILE), _final, INSTRUCTIONS, mode="replay", acquire=_acquire
    )
    return {role: AcquisitionAnalysisV1.model_validate_json(files[f"{role}.json"]) for role in RESEARCH_ROLES}


def _delta(previous: AcquisitionAnalysisV1, replacements: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    return {
        "kind": "delta",
        "requests": [],
        "state": "New original evidence changes the selected assessment fields.",
        "payload": None,
        "base_artifact_sha256": sha256(serialize(previous)).hexdigest(),
        "patches": [{"json_pointer": pointer, "value_json": json.dumps(value)} for pointer, value in replacements],
    }


@pytest.mark.parametrize("role", RESEARCH_ROLES)
def test_every_role_accepts_a_hash_bound_delta_without_mutating_its_base(
    previous_artifacts: dict[str, AcquisitionAnalysisV1], role: str
) -> None:
    """Specialists, organizers, synthesis and review preserve untouched content."""
    previous = previous_artifacts[role]
    before = serialize(previous)
    expected = previous.payload.model_dump(mode="json")
    expected["research"]["queries"][0] = "Updated targeted evidence query"
    result = resolve_delta(_delta(previous, [("/research/queries/0", "Updated targeted evidence query")]), previous)
    assert result == expected
    assert serialize(previous) == before


def test_delta_replaces_complete_collections_and_nullable_values(
    previous_artifacts: dict[str, AcquisitionAnalysisV1],
) -> None:
    """Collection additions and deletions use one replacement without index shifts."""
    previous = previous_artifacts["track_a_capital"]
    request = _delta(
        previous,
        [
            ("/horizons/0/missing_information", ["A newly disclosed qualification."]),
            ("/research/unavailable_information", []),
            ("/research/sources/0/retrieved_at", None),
        ],
    )
    result = resolve_delta(request, previous)
    horizons = cast(list[dict[str, JsonValue]], result["horizons"])
    assert horizons[0]["missing_information"] == ["A newly disclosed qualification."]
    research = cast(dict[str, JsonValue], result["research"])
    assert research["unavailable_information"] == []
    assert cast(list[dict[str, JsonValue]], research["sources"])[0]["retrieved_at"] is None


@pytest.mark.parametrize("role", RESEARCH_ROLES)
def test_every_role_can_adopt_only_its_exact_previous_payload(
    previous_artifacts: dict[str, AcquisitionAnalysisV1], role: str
) -> None:
    """A short unchanged assessment cannot sneak in replacement content."""
    previous = previous_artifacts[role]
    data: dict[str, JsonValue] = {
        "kind": "unchanged",
        "requests": [],
        "state": "The changed original corroborates all existing conclusions and qualifications.",
        "payload": None,
    }
    assert resolve_adoption(data, previous) == previous.payload.model_dump(mode="json")
    data["payload"] = previous.payload.model_dump(mode="json")
    with pytest.raises(ValidationError, match="delta_reassessment_rationale_required"):
        resolve_adoption(data, previous)


def test_reassessment_requires_a_previous_artifact_and_matching_hash(
    previous_artifacts: dict[str, AcquisitionAnalysisV1],
) -> None:
    """Deltas cannot be applied on the first pass or to another artifact version."""
    previous = previous_artifacts["track_a_capital"]
    data = _delta(previous, [("/horizons/0/rationale", "Updated rationale.")])
    with pytest.raises(ValueError, match="delta_previous_analysis_required"):
        resolve_delta(data, None)
    with pytest.raises(ValueError, match="delta_previous_analysis_required"):
        resolve_adoption({"kind": "unchanged"}, None)
    data["base_artifact_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="delta_base_artifact_mismatch"):
        resolve_delta(data, previous)


@pytest.mark.parametrize(
    "pointer",
    [
        "",
        "horizons/0/rationale",
        "/horizons/~2/rationale",
        "/horizons/0/rationale~",
        "/missing",
        "/horizons/2/rationale",
        "/horizons/01/rationale",
        "/horizons/-1/rationale",
        "/horizons/+/rationale",
        "/horizons/-/rationale",
        "/horizons/٠/rationale",
        "/horizons/0/rationale/child",
    ],
)
def test_delta_rejects_invalid_or_nonexistent_paths(
    previous_artifacts: dict[str, AcquisitionAnalysisV1], pointer: str
) -> None:
    """Only canonical indexes and existing payload nodes can be replaced."""
    previous = previous_artifacts["track_a_capital"]
    with pytest.raises(ValueError, match="delta_patch_path_invalid"):
        resolve_delta(_delta(previous, [(pointer, "Updated rationale.")]), previous)


@pytest.mark.parametrize(
    "paths",
    [
        ["/horizons/0/rationale", "/horizons/0/rationale"],
        ["/horizons", "/horizons/0/rationale"],
        ["/horizons/0/rationale", "/horizons"],
    ],
)
def test_delta_rejects_duplicate_and_parent_child_paths(
    previous_artifacts: dict[str, AcquisitionAnalysisV1], paths: list[str]
) -> None:
    """Patch order cannot silently overwrite another replacement."""
    previous = previous_artifacts["track_a_capital"]
    with pytest.raises(ValueError, match="delta_patch_paths_overlap"):
        resolve_delta(_delta(previous, [(path, "Updated.") for path in paths]), previous)


def test_pointer_escapes_follow_json_pointer_decoding_order() -> None:
    """An escaped slash remains one component and an escaped tilde is decoded once."""
    assert _pointer_parts("/a~1b/~0key/~01") == ("a/b", "~key", "~1")


@pytest.mark.parametrize("value_json", ["unquoted text", "{", "NaN", "Infinity"])
def test_delta_rejects_invalid_json_replacement_values(
    previous_artifacts: dict[str, AcquisitionAnalysisV1], value_json: str
) -> None:
    """JSON-encoded replacements do not admit nonstandard numeric constants."""
    previous = previous_artifacts["track_a_capital"]
    data = _delta(previous, [("/horizons/0/rationale", None)])
    cast(list[dict[str, JsonValue]], data["patches"])[0]["value_json"] = value_json
    with pytest.raises(ValueError, match="delta_patch_value_invalid"):
        resolve_delta(data, previous)


@pytest.mark.parametrize(
    ("pointer", "value"),
    [
        ("/horizons/0/rationale", 123),
        ("/horizons", []),
        ("/research/sources/0/url", "https://user:secret@example.com/filing"),
        ("/research/sources/0", {"unknown": "Not a valid source"}),
    ],
)
def test_reconstructed_payload_still_requires_the_full_role_schema(
    previous_artifacts: dict[str, AcquisitionAnalysisV1], pointer: str, value: JsonValue
) -> None:
    """A structurally applicable replacement cannot bypass field or source validation."""
    previous = previous_artifacts["track_a_capital"]
    with pytest.raises(ValidationError):
        resolve_delta(_delta(previous, [(pointer, value)]), previous)


@pytest.mark.parametrize("model", [DeltaSpecialistTurn, DeltaAggregateTurn, DeltaMainTurn, DeltaReviewTurn])
def test_delta_turn_schema_keeps_arbitrary_replacement_objects_json_encoded(
    model: type[DeltaSpecialistTurn | DeltaAggregateTurn | DeltaMainTurn | DeltaReviewTurn],
) -> None:
    """The provider receives a closed patch object rather than an arbitrary-key schema."""
    schema = _codex_output_schema(model.model_json_schema())
    definitions = cast(dict[str, dict[str, object]], schema["$defs"])
    patch = definitions["PayloadReplacement"]
    assert patch["additionalProperties"] is False
    properties = cast(dict[str, dict[str, object]], patch["properties"])
    assert properties["value_json"]["type"] == "string"
    assert set(cast(list[str], patch["required"])) == {"json_pointer", "value_json"}


@pytest.mark.parametrize("change", [{"patches": []}, {"base_artifact_sha256": None}, {"state": " "}])
def test_delta_requires_changes_base_hash_and_rationale(
    previous_artifacts: dict[str, AcquisitionAnalysisV1], change: dict[str, JsonValue]
) -> None:
    """An ambiguous or unexplained partial revision is never accepted."""
    previous = previous_artifacts["track_a_capital"]
    data = _delta(previous, [("/horizons/0/rationale", "Updated rationale.")])
    data.update(change)
    with pytest.raises(ValidationError):
        resolve_delta(data, previous)
