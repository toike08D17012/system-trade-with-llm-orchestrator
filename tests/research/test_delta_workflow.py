"""Exercise versioned delta responses through full validation and offline reconstruction."""

import json
from typing import cast

import pytest
from pydantic import JsonValue

from stock_research_llm_orchestrator.agents.configuration import load_agent_configuration
from stock_research_llm_orchestrator.agents.research_provider import WebTurnResult
from stock_research_llm_orchestrator.research.acquisition import replay_saved_bundle, run_workflow, validate_bundle
from stock_research_llm_orchestrator.research.workflow import ResearchRequest

from .test_acquisition import INSTRUCTIONS, RESEARCH_ROLES, _acquire, _acquisition_turn, _final
from .test_configured import PROFILE
from .test_workflow import _inputs


def _delta(request: ResearchRequest, *, invalid_evidence: bool = False) -> WebTurnResult:
    previous = cast(dict[str, JsonValue], request.data["own_previous_analysis"])
    old_payload = cast(dict[str, JsonValue], previous["payload"])
    payload = json.loads(_final(request).body)["payload"]
    payload["research"]["unavailable_information"] = ["Updated limitation after reading the new filing."]
    if invalid_evidence:
        payload["horizons"][0]["claims"][0]["evidence_ids"] = ["not-in-current-snapshot"]
    patches = [
        {"json_pointer": "/" + key, "value_json": json.dumps(value)}
        for key, value in payload.items()
        if value != old_payload[key]
    ]
    return WebTurnResult(
        json.dumps(
            {
                "kind": "delta",
                "requests": [],
                "state": "The new filing updates a limitation; unchanged conclusions are retained.",
                "payload": None,
                "base_artifact_sha256": request.data["own_previous_artifact_sha256"],
                "patches": patches,
            }
        ).encode(),
        b"",
    )


def test_every_reassessing_role_supports_delta_and_exact_offline_replay() -> None:
    """The raw delta is retained while all eight complete role artifacts are reconstructed."""
    acquired = False
    revised: set[str] = set()

    def invoke(request: ResearchRequest) -> WebTurnResult:
        nonlocal acquired
        if request.stage == "primary_reviewer" and not acquired:
            acquired = True
            return _acquisition_turn()
        if request.stage != "translation" and request.data["evidence_epoch"] == 2:
            revised.add(request.stage)
            return _delta(request)
        return _final(request)

    config = load_agent_configuration(PROFILE)
    files = run_workflow(
        _inputs(),
        config,
        invoke,
        INSTRUCTIONS,
        mode="replay",
        acquire=_acquire,
        execution_profile="parallel_incremental_v2",
        reassessment_from_epoch=2,
        stable_evidence_from_epoch=1,
        batch_common_updates_from_epoch=1,
    )
    assert revised == set(RESEARCH_ROLES)
    decisions = json.loads(files["incremental-decisions.json"])
    assert len(decisions) == 8
    assert all(item["response_kind"] == "delta" and item["updated_paths"] for item in decisions)
    for role in RESEARCH_ROLES:
        payload = json.loads(files[f"{role}.json"])["payload"]
        assert payload["research"]["unavailable_information"] == ["Updated limitation after reading the new filing."]
    validate_bundle(files)
    assert replay_saved_bundle(files, _inputs(), config) == files
    tampered = dict(files)
    response = json.loads(tampered["responses/track_a_capital-1.json"])
    response["base_artifact_sha256"] = "0" * 64
    tampered["responses/track_a_capital-1.json"] = json.dumps(response).encode()
    with pytest.raises(ValueError):
        validate_bundle(tampered)


def test_delta_cannot_bypass_evidence_validation() -> None:
    """A well-shaped replacement still fails if it introduces an unknown evidence identity."""
    acquired = False

    def invoke(request: ResearchRequest) -> WebTurnResult:
        nonlocal acquired
        if request.stage == "primary_reviewer" and not acquired:
            acquired = True
            return _acquisition_turn()
        if request.stage == "track_a_capital" and request.data["evidence_epoch"] == 2:
            return _delta(request, invalid_evidence=True)
        return _final(request)

    with pytest.raises(ValueError, match="configured_evidence_reference_invalid"):
        run_workflow(
            _inputs(),
            load_agent_configuration(PROFILE),
            invoke,
            INSTRUCTIONS,
            mode="replay",
            acquire=_acquire,
            execution_profile="parallel_incremental_v2",
            reassessment_from_epoch=2,
            stable_evidence_from_epoch=1,
            batch_common_updates_from_epoch=1,
        )


def test_downstream_unchanged_is_revalidated_against_current_dependencies() -> None:
    """Adoption rebuilds current role artifacts and remains subject to downstream validation."""
    acquired = False

    def invoke(request: ResearchRequest) -> WebTurnResult:
        nonlocal acquired
        if request.stage == "primary_reviewer" and not acquired:
            acquired = True
            return _acquisition_turn()
        if request.stage != "translation" and request.data["evidence_epoch"] == 2:
            return WebTurnResult(
                json.dumps(
                    {
                        "kind": "unchanged",
                        "requests": [],
                        "payload": None,
                        "state": "The new filing corroborates all existing assumptions and dependencies.",
                        "base_artifact_sha256": None,
                        "patches": [],
                    }
                ).encode(),
                b"",
            )
        return _final(request)

    files = run_workflow(
        _inputs(),
        load_agent_configuration(PROFILE),
        invoke,
        INSTRUCTIONS,
        mode="replay",
        acquire=_acquire,
        execution_profile="parallel_incremental_v2",
        reassessment_from_epoch=2,
        stable_evidence_from_epoch=1,
        batch_common_updates_from_epoch=1,
    )
    assert all(item["decision"] == "unchanged" for item in json.loads(files["incremental-decisions.json"]))
    for role in RESEARCH_ROLES:
        assert json.loads(files[f"epochs/1/{role}.json"])["payload"] == json.loads(files[f"{role}.json"])["payload"]
