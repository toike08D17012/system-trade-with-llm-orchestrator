"""Exercise concurrent isolated research and deterministic coordinator commits."""

import json
from collections.abc import Generator
from pathlib import Path
from threading import Barrier, Event, Lock

import pytest

from stock_research_llm_orchestrator.agents.configuration import load_agent_configuration
from stock_research_llm_orchestrator.agents.research_provider import WebTurnResult
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.acquisition_analysis import (
    AcquisitionRequest,
    AcquisitionResult,
)
from stock_research_llm_orchestrator.research.acquisition import replay_saved_bundle, run_workflow, validate_bundle
from stock_research_llm_orchestrator.research.configured import SPECIALISTS
from stock_research_llm_orchestrator.research.resume import load_completed_turns
from stock_research_llm_orchestrator.research.scheduling import run_group
from stock_research_llm_orchestrator.research.workflow import ResearchRequest

from .test_acquisition import INSTRUCTIONS, _acquire, _acquisition_turn, _final
from .test_completed_resume import CAPTURED_AT, SNAPSHOT_AT, _saved_turn
from .test_configured import PROFILE
from .test_workflow import _inputs


PROFILE_NAME = "parallel_incremental_v1"


def test_specialists_run_simultaneously_with_private_inputs_and_exact_replay() -> None:
    """A barrier requires all four isolated specialists to enter before any can finish."""
    barrier = Barrier(4, timeout=10)
    lock = Lock()
    initial_roles: list[str] = []

    def invoke(request: ResearchRequest) -> WebTurnResult:
        if request.stage in SPECIALISTS:
            assert "specialists" not in request.data
            assert "summaries" not in request.data
            assert "own_previous_analysis" not in request.data
            with lock:
                initial_roles.append(request.stage)
            barrier.wait()
        return _final(request)

    files = run_workflow(
        _inputs(),
        load_agent_configuration(PROFILE),
        invoke,
        INSTRUCTIONS,
        mode="replay",
        acquire=_acquire,
        execution_profile=PROFILE_NAME,
    )

    assert set(initial_roles) == set(SPECIALISTS)
    assert json.loads(files["manifest.json"])["epochs"] == 1
    assert json.loads(files["manifest.json"])["execution_profile"] == PROFILE_NAME
    validate_bundle(files)
    assert replay_saved_bundle(files, _inputs(), load_agent_configuration(PROFILE)) == files


def test_parallel_acquisition_is_serially_committed_and_unchanged_is_auditable() -> None:
    """New neutral evidence is shared before four explicit delta assessments adopt prior payloads."""
    acquired = False
    assessments: list[str] = []
    lock = Lock()

    def invoke(request: ResearchRequest) -> WebTurnResult:
        nonlocal acquired
        if request.stage == SPECIALISTS[0] and request.data["evidence_epoch"] == 1:
            with lock:
                if not acquired:
                    acquired = True
                    return _acquisition_turn()
        if request.stage in SPECIALISTS and request.data["evidence_epoch"] == 2:
            assert request.data["new_common_evidence_ids"] == ["acquired-filing"]
            assert request.data["own_previous_analysis"]
            with lock:
                assessments.append(request.stage)
            return WebTurnResult(
                json.dumps(
                    {
                        "kind": "unchanged",
                        "requests": [],
                        "payload": None,
                        "state": "The new neutral filing does not change the existing scenario assumptions.",
                    }
                ).encode(),
                b"",
            )
        if request.stage in {"orchestrator", "primary_reviewer"}:
            decisions = request.data["incremental_assessments"]
            assert isinstance(decisions, list) and len(decisions) == 4
        return _final(request)

    files = run_workflow(
        _inputs(),
        load_agent_configuration(PROFILE),
        invoke,
        INSTRUCTIONS,
        mode="replay",
        acquire=_acquire,
        execution_profile=PROFILE_NAME,
        reassessment_from_epoch=2,
        stable_evidence_from_epoch=1,
        batch_common_updates_from_epoch=1,
    )

    assert set(assessments) == set(SPECIALISTS)
    assert len(json.loads(files["acquisition-log.json"])) == 1
    assert all(item["decision"] == "unchanged" for item in json.loads(files["incremental-decisions.json"]))
    validate_bundle(files)
    assert replay_saved_bundle(files, _inputs(), load_agent_configuration(PROFILE)) == files


def test_failed_parallel_call_does_not_cancel_successful_sibling() -> None:
    """The failed wave waits until the other provider has saved its successful response."""
    barrier = Barrier(2, timeout=10)
    success = Event()
    config = load_agent_configuration(PROFILE)

    def execution(role: str) -> Generator[ResearchRequest, WebTurnResult]:
        yield ResearchRequest(role, "codex", "Research independently.", {}, type(config), config.agents["gpt"])

    def invoke(request: ResearchRequest) -> WebTurnResult:
        barrier.wait()
        if request.stage == "failed":
            raise RuntimeError("provider_failure")
        success.set()
        return WebTurnResult(b"{}", b"")

    with pytest.raises(RuntimeError, match="provider_failure"):
        run_group([execution("failed"), execution("successful")], invoke, concurrent=True)
    assert success.is_set()


def test_parallel_resume_retains_pending_snapshot_and_completed_sibling(tmp_path: Path) -> None:
    """Stable identities recover a completed sibling after a failed journal entry."""
    failed = _saved_turn(tmp_path, 0, SPECIALISTS[0], SPECIALISTS[0] + "-0")
    (failed / "failure.json").write_text('{"status":"incomplete"}')
    _saved_turn(tmp_path, 1, SPECIALISTS[1], SPECIALISTS[1] + "-0")

    turns, stamps = load_completed_turns(tmp_path, include_pending_snapshots=True)

    assert set(turns) == {SPECIALISTS[1] + "-0"}
    assert stamps["snapshot-" + SPECIALISTS[0] + "-0"] == SNAPSHOT_AT
    assert stamps["snapshot-" + SPECIALISTS[1] + "-0"] == SNAPSHOT_AT
    assert stamps["artifact-" + SPECIALISTS[1] + "-0"] == CAPTURED_AT


def test_parallel_resume_recovers_unchanged_artifact_timestamp(tmp_path: Path) -> None:
    """Unchanged is a completed materialized analysis and needs an artifact stamp."""
    directory = _saved_turn(tmp_path, 0, SPECIALISTS[0], SPECIALISTS[0] + "-1")
    (directory / "response.json").write_text(
        '{"kind":"unchanged","requests":[],"payload":null,"state":"The prior analysis still applies."}'
    )
    (directory / "source-timestamps.json").write_text(json.dumps({"provider_response_captured_at": CAPTURED_AT}))
    turns, stamps = load_completed_turns(tmp_path, include_pending_snapshots=True)
    assert SPECIALISTS[0] + "-1" in turns
    assert stamps["artifact-" + SPECIALISTS[0] + "-1"] == CAPTURED_AT


def test_reverse_completion_commits_shared_acquisition_once_in_role_order() -> None:
    """Response completion order cannot change shared acquisition identities or cache reuse."""
    barrier = Barrier(4, timeout=10)
    finished = {role: Event() for role in SPECIALISTS}
    completion_order: list[str] = []
    network_roles: list[str] = []
    lock = Lock()

    def invoke(request: ResearchRequest) -> WebTurnResult:
        if request.stage in SPECIALISTS:
            if request.data["evidence_epoch"] == 1 and "last_acquisition_results" not in request.data:
                barrier.wait()
                position = SPECIALISTS.index(request.stage)
                if position < len(SPECIALISTS) - 1:
                    assert finished[SPECIALISTS[position + 1]].wait(timeout=10)
                with lock:
                    completion_order.append(request.stage)
                finished[request.stage].set()
                return _acquisition_turn("Read the same original filing.")
            if request.data["evidence_epoch"] == 2:
                return WebTurnResult(
                    json.dumps(
                        {
                            "kind": "unchanged",
                            "requests": [],
                            "payload": None,
                            "state": "The shared original confirms the prior assumptions "
                            "without changing the analysis.",
                        }
                    ).encode(),
                    b"",
                )
        return _final(request)

    def acquire(request: AcquisitionRequest, role: str, turn: int) -> tuple[AcquisitionResult, dict[str, bytes]]:
        network_roles.append(role)
        return _acquire(request, role, turn)

    files = run_workflow(
        _inputs(),
        load_agent_configuration(PROFILE),
        invoke,
        INSTRUCTIONS,
        mode="replay",
        acquire=acquire,
        execution_profile=PROFILE_NAME,
        reassessment_from_epoch=2,
        stable_evidence_from_epoch=1,
        batch_common_updates_from_epoch=1,
    )

    assert completion_order == list(reversed(SPECIALISTS))
    assert network_roles == [SPECIALISTS[0]]
    log = json.loads(files["acquisition-log.json"])
    assert [entry["role"] for entry in log] == list(SPECIALISTS)
    assert [entry["reused_index"] for entry in log] == [None, 0, 0, 0]
    validate_bundle(files)
