"""Recover only actual completed turns and continue public runs without repeating their calls."""

import json
import os
from datetime import datetime
from pathlib import Path
from threading import Barrier
from typing import Any

import pytest

from stock_research_llm_orchestrator import cli
from stock_research_llm_orchestrator.agents.research_provider import ProviderExecutionError, WebTurnResult
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.acquisition_analysis import (
    AcquisitionRequest,
    AcquisitionResult,
)
from stock_research_llm_orchestrator.preparation import research_acquisition
from stock_research_llm_orchestrator.research import baseline
from stock_research_llm_orchestrator.research.resume import load_completed_turns
from stock_research_llm_orchestrator.research.workflow import ResearchRequest

from .test_acquisition import RESEARCH_ROLES, _final
from .test_baseline import _discovery
from .test_configured import PROFILE
from .test_workflow import _source


SNAPSHOT_AT = "2026-11-15T00:00:00+00:00"
CAPTURED_AT = "2026-11-15T00:01:00+00:00"


def _saved_turn(
    root: Path, index: int, stage: str, key: str, *, snapshot_at: str = SNAPSHOT_AT, captured_at: str = CAPTURED_AT
) -> Path:
    directory = root / f"{index:04d}-{stage}"
    directory.mkdir(parents=True)
    (directory / "request.json").write_text(
        json.dumps(
            {
                "stage": stage,
                "data": {
                    "evidence_snapshot": {"reference": {"artifact_id": key}, "created_at": snapshot_at},
                },
            }
        )
    )
    response = directory / "response.json"
    response.write_text('{"kind":"final"}')
    captured = datetime.fromisoformat(captured_at).timestamp()
    os.utime(response, (captured, captured))
    (directory / "trace.jsonl").write_bytes(b'{"type":"item.started","item":{"type":"web_search"}}\n')
    return directory


def test_completed_loader_excludes_failed_missing_and_discovery_outputs(tmp_path: Path) -> None:
    """A body written before validation failure is not a successful checkpoint."""
    completed = _saved_turn(tmp_path, 0, "track_a_capital", "track_a_capital-0")
    failed = _saved_turn(tmp_path, 1, "track_a_income", "track_a_income-0")
    (failed / "failure.json").write_text('{"status":"incomplete"}')
    missing = _saved_turn(tmp_path, 2, "track_b_capital", "track_b_capital-0")
    (missing / "response.json").unlink()
    _saved_turn(tmp_path, 3, "baseline_discovery", "not-a-role-turn")

    turns, stamps = load_completed_turns(tmp_path)

    assert set(turns) == {"track_a_capital-0"}
    retained = turns["track_a_capital-0"]
    assert retained.request == (completed / "request.json").read_bytes()
    assert retained.response == (completed / "response.json").read_bytes()
    assert retained.trace == (completed / "trace.jsonl").read_bytes()
    assert retained.source == completed
    assert retained.captured_at == CAPTURED_AT
    assert stamps == {"snapshot-track_a_capital-0": SNAPSHOT_AT, "artifact-track_a_capital-0": CAPTURED_AT}


def test_completed_loader_preserves_original_capture_and_artifact_times_after_copy(tmp_path: Path) -> None:
    """Copying a retained result must not change its declared original response capture."""
    directory = _saved_turn(tmp_path, 0, "track_a_capital", "track_a_capital-0")
    original_capture = "2026-11-15T00:00:30+00:00"
    artifact_created = "2026-11-15T00:00:31+00:00"
    (directory / "source-timestamps.json").write_text(
        json.dumps({"provider_response_captured_at": original_capture, "artifact_created_at": artifact_created})
    )

    turns, stamps = load_completed_turns(tmp_path)

    assert turns["track_a_capital-0"].captured_at == original_capture
    assert stamps["artifact-track_a_capital-0"] == artifact_created


def test_completed_loader_retains_summary_without_snapshot_and_delta_creation_time(tmp_path: Path) -> None:
    """Document summaries are reusable turns and delta artifacts keep their original capture time."""
    summary = tmp_path / ("0000-document_summary-" + "a" * 64)
    summary.mkdir()
    stage = "document_summary-" + "a" * 64
    (summary / "request.json").write_text(json.dumps({"stage": stage, "data": {"text_sha256": "b" * 64}}))
    (summary / "response.json").write_text('{"overview":"Navigation only"}')
    (summary / "trace.jsonl").write_bytes(b"")
    (summary / "source-timestamps.json").write_text(json.dumps({"provider_response_captured_at": CAPTURED_AT}))
    delta = _saved_turn(tmp_path, 1, "track_a_capital", "track_a_capital-1")
    (delta / "response.json").write_text('{"kind":"delta"}')
    (delta / "source-timestamps.json").write_text(json.dumps({"provider_response_captured_at": CAPTURED_AT}))
    turns, stamps = load_completed_turns(tmp_path)
    assert set(turns) == {stage, "track_a_capital-1"}
    assert turns[stage].captured_at == CAPTURED_AT
    assert "snapshot-" + stage not in stamps
    assert stamps["artifact-track_a_capital-1"] == CAPTURED_AT


def test_downstream_inputs_recover_exact_specialist_summary_and_main_creation_times(tmp_path: Path) -> None:
    """Actual serialized upstream artifacts override fallback response-capture timestamps."""
    keys = ("track_a_capital-0", "track_a_aggregator-0", "orchestrator-0")
    for index, stage in enumerate(("track_a_capital", "track_a_aggregator", "orchestrator")):
        _saved_turn(
            tmp_path,
            index,
            stage,
            keys[index],
            snapshot_at=f"2026-11-15T00:{index * 2:02d}:00+00:00",
            captured_at=f"2026-11-15T00:{index * 2 + 1:02d}:00+00:00",
        )
    reviewer = _saved_turn(
        tmp_path,
        3,
        "primary_reviewer",
        "primary_reviewer-0",
        snapshot_at="2026-11-15T00:06:00+00:00",
        captured_at="2026-11-15T00:07:00+00:00",
    )
    record = json.loads((reviewer / "request.json").read_bytes())
    exact = {key: f"2026-11-15T00:{index * 2 + 1:02d}:10+00:00" for index, key in enumerate(keys)}

    def artifact(key: str) -> dict[str, object]:
        return {"evidence_snapshot_reference": {"artifact_id": key}, "created_at": exact[key]}

    record["data"].update(
        specialists={"track_a_capital": artifact(keys[0])},
        summaries={"track_a_aggregator": artifact(keys[1])},
        main=artifact(keys[2]),
    )
    (reviewer / "request.json").write_text(json.dumps(record))

    _, stamps = load_completed_turns(tmp_path)

    for key, created_at in exact.items():
        assert stamps["artifact-" + key] == created_at


@pytest.mark.parametrize("capture", ["2026-11-14T23:59:59+00:00", "not-a-timestamp"])
def test_completed_loader_rejects_invalid_capture_time(tmp_path: Path, capture: str) -> None:
    """A checkpoint cannot claim its response was captured before the supplied snapshot."""
    directory = _saved_turn(tmp_path, 0, "track_a_capital", "track_a_capital-0")
    (directory / "source-timestamps.json").write_text(json.dumps({"provider_response_captured_at": capture}))
    with pytest.raises(ValueError):
        load_completed_turns(tmp_path)


def test_completed_loader_rejects_duplicate_snapshot_turn_identity(tmp_path: Path) -> None:
    """A later directory cannot replace the provider output for an existing snapshot key."""
    _saved_turn(tmp_path, 0, "track_a_capital", "same-turn")
    _saved_turn(tmp_path, 1, "track_a_capital", "same-turn")
    with pytest.raises(ValueError, match="research_resume_duplicate_turn"):
        load_completed_turns(tmp_path)


def test_public_completed_resume_reuses_exact_success_then_continues_and_replays_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Parallel interruption preserves successful siblings and resumes only the failed specialist."""
    task, prior = _source()
    task_path = tmp_path / "task.json"
    task_path.write_bytes(task)
    prior_root = tmp_path / "prior"
    for name, body in prior.items():
        path = prior_root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    runtime = tmp_path / "runtime"
    completed_calls: list[str] = []

    def executor_factory(**_kwargs: Any) -> object:
        def acquire(request: AcquisitionRequest, role: str, _turn: int) -> tuple[AcquisitionResult, dict[str, bytes]]:
            assert role == "baseline"
            return AcquisitionResult(
                request_sha256=research_acquisition.request_sha256(request),
                status="not_attempted",
                validation="pending",
                requested_at="2026-11-14T00:00:00+00:00",
                completed_at="2026-11-14T00:00:00+00:00",
                detail="Deliberately unavailable synthetic baseline source.",
            ), {}

        return acquire

    barrier = Barrier(4, timeout=10)

    def interrupted_invoke(request: ResearchRequest) -> WebTurnResult:
        if request.stage == "baseline_discovery":
            return _discovery(())
        barrier.wait()
        if request.stage == "track_a_income":
            raise ProviderExecutionError(b'{"type":"error","message":"usage limit"}\n', 1, "quota_exceeded")
        assert request.stage in RESEARCH_ROLES[:4]
        completed_calls.append(request.stage)
        return _final(request)

    monkeypatch.setattr(research_acquisition, "ResearchAcquisitionExecutor", executor_factory)
    monkeypatch.setattr("stock_research_llm_orchestrator.research.cli.preflight_research", lambda *_: None)
    monkeypatch.setattr("stock_research_llm_orchestrator.research.cli.invoke_web_research", interrupted_invoke)
    common = [
        "--agents",
        str(PROFILE),
        "--task",
        str(task_path),
        "--prior",
        str(prior_root),
        "--runs",
        str(tmp_path),
    ]
    assert cli.main(["research", "run", *common, "--runtime", str(runtime), "--destination", "interrupted"]) != 0
    successful_specialists = {"track_a_capital", "track_b_capital", "track_b_income"}
    assert set(completed_calls) == successful_specialists
    assert not (tmp_path / "interrupted").exists()
    journal = next((runtime / "research-executions").glob("interrupted-*"))
    retained, _ = load_completed_turns(journal)
    assert set(retained) == {role + "-0" for role in successful_specialists}
    failed = next(journal.glob("*-track_a_income"))
    assert json.loads((failed / "provider-failure.json").read_bytes())["diagnostic_code"] == "quota_exceeded"

    continued_calls: list[str] = []

    def continued_invoke(request: ResearchRequest) -> WebTurnResult:
        assert request.stage not in successful_specialists | {"baseline_discovery"}
        continued_calls.append(request.stage)
        return _final(request)

    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("Saved baseline and offline replay must not invoke a live dependency.")

    monkeypatch.setattr(baseline, "prepare_baseline", forbidden)
    monkeypatch.setattr("stock_research_llm_orchestrator.research.cli.invoke_web_research", continued_invoke)
    assert (
        cli.main(
            [
                "research",
                "run",
                *common,
                "--runtime",
                str(runtime),
                "--destination",
                "continued",
                "--resume-from",
                str(journal),
                "--resume-completed-turns",
            ]
        )
        == 0
    )
    completed = tmp_path / "continued"
    assert set(continued_calls) == (set(RESEARCH_ROLES) - successful_specialists) | {"translation"}
    origins = json.loads((completed / "execution-origins.json").read_bytes())
    retained_keys = {role + "-0" for role in successful_specialists}
    assert {key for key, value in origins.items() if value == "retained_exact_request"} == retained_keys
    assert set(value for key, value in origins.items() if key not in retained_keys) == {"live"}
    assert (completed / "retained-requests/track_a_capital-0.json").read_bytes() == retained[
        "track_a_capital-0"
    ].request
    assert (completed / "report.md").is_file()

    monkeypatch.setattr(research_acquisition, "ResearchAcquisitionExecutor", forbidden)
    monkeypatch.setattr("stock_research_llm_orchestrator.research.cli.preflight_research", forbidden)
    monkeypatch.setattr("stock_research_llm_orchestrator.research.cli.invoke_web_research", forbidden)
    monkeypatch.setattr("stock_research_llm_orchestrator.research.cli.invoke_research", forbidden)
    assert cli.main(["research", "validate", "--bundle", str(completed)]) == 0
    frozen = json.loads((completed / "input/evidence-set.json").read_bytes())["evidence_frozen_at"]
    assert (
        cli.main(
            [
                "research",
                "replay",
                *common,
                "--destination",
                "offline",
                "--responses",
                str(completed),
                "--at",
                frozen,
            ]
        )
        == 0
    )
    assert (tmp_path / "offline/orchestrator.json").read_bytes() == (completed / "orchestrator.json").read_bytes()


def test_public_completed_resume_requires_explicit_source(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The reuse flag cannot silently start a fresh analysis without a checkpoint source."""
    monkeypatch.setattr(
        "stock_research_llm_orchestrator.research.cli.preflight_research",
        lambda *_: pytest.fail("Invalid resume arguments must fail before authentication."),
    )
    assert (
        cli.main(
            [
                "research",
                "run",
                "--task",
                str(tmp_path / "missing-task"),
                "--prior",
                str(tmp_path / "missing-prior"),
                "--runs",
                str(tmp_path),
                "--destination",
                "invalid",
                "--resume-completed-turns",
            ]
        )
        != 0
    )


def test_own_previous_analysis_recovers_exact_creation_time_for_incremental_resume(tmp_path: Path) -> None:
    """A later cohort's own artifact overrides the earlier response-capture timestamp."""
    _saved_turn(tmp_path, 0, "track_a_capital", "track_a_capital-0")
    later = _saved_turn(
        tmp_path,
        1,
        "track_a_capital",
        "track_a_capital-1",
        snapshot_at="2026-11-15T00:02:00+00:00",
        captured_at="2026-11-15T00:03:00+00:00",
    )
    exact_created = "2026-11-15T00:01:01+00:00"
    record = json.loads((later / "request.json").read_bytes())
    record["data"]["own_previous_analysis"] = {
        "evidence_snapshot_reference": {"artifact_id": "track_a_capital-0"},
        "created_at": exact_created,
    }
    (later / "request.json").write_text(json.dumps(record))

    turns, stamps = load_completed_turns(tmp_path)

    assert turns["track_a_capital-0"].captured_at == CAPTURED_AT
    assert stamps["artifact-track_a_capital-0"] == exact_created
    assert stamps["snapshot-track_a_capital-1"] == "2026-11-15T00:02:00+00:00"
    assert stamps["artifact-track_a_capital-1"] == "2026-11-15T00:03:00+00:00"
