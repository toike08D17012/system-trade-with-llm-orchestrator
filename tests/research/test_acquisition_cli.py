"""Exercise the public format-five live boundary and provider-free offline replay."""

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import JsonValue

from stock_research_llm_orchestrator import cli
from stock_research_llm_orchestrator.agents.configuration import load_agent_configuration
from stock_research_llm_orchestrator.agents.research_provider import WebTurnResult
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.acquisition_analysis import (
    AcquisitionRequest,
    AcquisitionResult,
    AcquisitionSpecialistTurn,
)
from stock_research_llm_orchestrator.preparation import research_acquisition
from stock_research_llm_orchestrator.research import acquisition, baseline, resume
from stock_research_llm_orchestrator.research.resume import RetainedTurn
from stock_research_llm_orchestrator.research.workflow import ResearchRequest

from .test_acquisition import RESEARCH_ROLES, _acquire, _acquisition_turn, _final
from .test_baseline import _discovery
from .test_configured import PROFILE
from .test_document_navigation import _summary
from .test_workflow import _inputs, _source


def test_public_live_default_prepares_baseline_and_supports_all_role_acquisitions_then_replays_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The public CLI uses format five, resumes every role, and replays without live dependencies."""
    task, prior = _source()
    task_path = tmp_path / "task.json"
    task_path.write_bytes(task)
    prior_root = tmp_path / "prior"
    for name, body in prior.items():
        path = prior_root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    baseline_calls: list[str] = []
    acquisitions: list[str] = []
    summary_calls: list[str] = []
    asked: set[str] = set()
    original_prepare = baseline.prepare_baseline

    def executor_factory(**kwargs: Any) -> object:
        assert kwargs["runtime"] == tmp_path / "runtime"

        def acquire(request: AcquisitionRequest, role: str, turn: int) -> tuple[AcquisitionResult, dict[str, bytes]]:
            acquisitions.append(role)
            if role == "baseline":
                return AcquisitionResult(
                    request_sha256=research_acquisition.request_sha256(request),
                    status="not_attempted",
                    validation="pending",
                    requested_at="2026-11-14T00:00:00Z",
                    completed_at="2026-11-14T00:00:00Z",
                    detail="Synthetic route deliberately unattempted.",
                ), {}
            return _acquire(request, role, turn)

        return acquire

    def prepare(*args: Any, **kwargs: Any) -> dict[str, bytes]:
        baseline_calls.append("prepared")
        return original_prepare(*args, **kwargs)

    def invoke(request: ResearchRequest) -> WebTurnResult:
        if request.stage == "baseline_discovery":
            return _discovery(())
        if request.stage.startswith("document_summary-"):
            summary_calls.append(request.stage)
            return _summary(request)
        if request.stage.startswith("reassessment-selection-"):
            return WebTurnResult(b'{"roles":[],"reason":"Retain the existing specialist analyses."}', b"")
        if request.stage in RESEARCH_ROLES and request.stage not in asked:
            asked.add(request.stage)
            return _acquisition_turn(request.stage)
        if request.data.get("own_previous_turns"):
            assert request.data["last_acquisition_results"]
        return _final(request)

    monkeypatch.setattr(research_acquisition, "ResearchAcquisitionExecutor", executor_factory)
    monkeypatch.setattr(baseline, "prepare_baseline", prepare)
    monkeypatch.setattr("stock_research_llm_orchestrator.research.cli.preflight_research", lambda *_: None)
    monkeypatch.setattr("stock_research_llm_orchestrator.research.cli.invoke_web_research", invoke)
    args = [
        "research",
        "run",
        "--agents",
        str(PROFILE),
        "--task",
        str(task_path),
        "--prior",
        str(prior_root),
        "--runs",
        str(tmp_path),
        "--destination",
        "live",
        "--runtime",
        str(tmp_path / "runtime"),
    ]
    assert cli.main(args) == 0
    live = tmp_path / "live"
    manifest = json.loads((live / "manifest.json").read_bytes())
    assert manifest["format_version"] == 5
    assert manifest["execution_profile"] == "parallel_incremental_v5"
    assert manifest["reassessment_from_epoch"] == 2
    assert manifest["stable_evidence_from_epoch"] == 1
    assert manifest["batch_common_updates_from_epoch"] == 1
    assert manifest["status"] == "completed"
    assert baseline_calls == ["prepared"]
    assert acquisitions[:2] == ["baseline", "baseline"]
    assert asked == set(RESEARCH_ROLES)
    log = json.loads((live / "acquisition-log.json").read_bytes())
    assert {entry["role"] for entry in log} == set(RESEARCH_ROLES)
    assert (live / "report.md").is_file()
    assert len(summary_calls) == 1
    asked.clear()
    cached_args = list(args)
    cached_args[cached_args.index("--destination") + 1] = "cached-live"
    assert cli.main(cached_args) == 0
    assert len(summary_calls) == 1
    cached_origins = json.loads((tmp_path / "cached-live/execution-origins.json").read_bytes())
    assert cached_origins[summary_calls[0]] == "retained_exact_request"

    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("Offline validation/replay must not acquire, authenticate, discover, or invoke a provider")

    monkeypatch.setattr(research_acquisition, "ResearchAcquisitionExecutor", forbidden)
    monkeypatch.setattr(baseline, "prepare_baseline", forbidden)
    monkeypatch.setattr("stock_research_llm_orchestrator.research.cli.preflight_research", forbidden)
    monkeypatch.setattr("stock_research_llm_orchestrator.research.cli.invoke_web_research", forbidden)
    monkeypatch.setattr("stock_research_llm_orchestrator.research.cli.invoke_research", forbidden)
    assert cli.main(["research", "validate", "--bundle", str(live)]) == 0
    frozen = json.loads((live / "input/evidence-set.json").read_bytes())["evidence_frozen_at"]
    assert (
        cli.main(
            [
                "research",
                "replay",
                "--agents",
                str(PROFILE),
                "--task",
                str(task_path),
                "--prior",
                str(prior_root),
                "--runs",
                str(tmp_path),
                "--destination",
                "offline",
                "--responses",
                str(live),
                "--at",
                frozen,
            ]
        )
        == 0
    )
    replay = tmp_path / "offline"
    assert (replay / "orchestrator.json").read_bytes() == (live / "orchestrator.json").read_bytes()
    assert json.loads((replay / "manifest.json").read_bytes())["mode"] == "replay"


@pytest.mark.parametrize(
    ("saved_epoch", "expected_epoch", "saved_stable_epoch", "expected_stable_epoch", "saved_batch_epoch"),
    [(None, 5, None, 5, None), (2, 2, None, 5, None), (2, 2, 1, 1, None), (2, 2, 1, 1, 3)],
)
@pytest.mark.parametrize(
    "saved_profile", [None, "parallel_incremental_v1", "parallel_incremental_v2", "parallel_incremental_v3"]
)
def test_completed_resume_preserves_exact_prefix_and_selects_recorded_or_legacy_reassessment_epoch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    saved_epoch: int | None,
    expected_epoch: int,
    saved_stable_epoch: int | None,
    expected_stable_epoch: int,
    saved_batch_epoch: int | None,
    saved_profile: str | None,
) -> None:
    """CLI dispatch keeps old request bytes and uses the maximum retained epoch only for legacy journals."""
    task, prior = _source()
    task_path = tmp_path / "task.json"
    task_path.write_bytes(task)
    prior_root = tmp_path / "prior"
    for name, body in prior.items():
        path = prior_root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    source = tmp_path / "checkpoint"
    source.mkdir()
    if saved_epoch is not None or saved_profile is not None:
        options: dict[str, JsonValue] = {"reassessment_from_epoch": saved_epoch}
        if saved_epoch is None:
            options["reassessment_from_epoch"] = 5
        if saved_profile is not None:
            options["execution_profile"] = saved_profile
        if saved_stable_epoch is not None:
            options["stable_evidence_from_epoch"] = saved_stable_epoch
        if saved_batch_epoch is not None:
            options["batch_common_updates_from_epoch"] = saved_batch_epoch
        (source / "runtime-options.json").write_text(json.dumps(options))
    inputs = _inputs()
    config = load_agent_configuration(PROFILE)
    turns: dict[str, RetainedTurn] = {}
    requests: list[ResearchRequest] = []
    for stage, key, epoch in (
        ("track_a_capital", "track_a_capital-7", 4),
        ("track_a_income", "track_a_income-0", 1),
        ("track_b_capital", "track_b_capital-3", 2),
    ):
        data: dict[str, JsonValue] = {
            "evidence_epoch": epoch,
            "evidence_snapshot": {
                "reference": {"artifact_id": key},
                "created_at": "2026-11-15T00:00:00+00:00",
            },
        }
        request = ResearchRequest(
            stage, "codex", "Retained original instructions.", data, AcquisitionSpecialistTurn, config.agents["gpt"]
        )
        requests.append(request)
        record = {
            "stage": stage,
            "instructions": request.instructions,
            "data": data,
            "output_schema": request.response_model.model_json_schema(),
            "agent": config.agents["gpt"].model_dump(mode="json"),
        }
        output = _final(request)
        turns[key] = RetainedTurn(
            json.dumps(record).encode(), output.body, output.trace, source / stage, "2026-11-15T00:01:00+00:00"
        )
    original_prefix = turns["track_a_capital-7"].request
    stamps = {"snapshot-track_a_capital-7": "2026-11-15T00:00:00+00:00"}
    selected: list[int] = []

    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("Retained-prefix dispatch must not invoke a new provider or prepare another baseline.")

    def capture_dispatch(*args: Any, **kwargs: Any) -> dict[str, bytes]:
        assert kwargs.get("execution_profile") == saved_profile
        selected.append(kwargs["reassessment_from_epoch"])
        assert kwargs["stable_evidence_from_epoch"] == expected_stable_epoch
        assert kwargs["batch_common_updates_from_epoch"] == (saved_batch_epoch or 5)
        assert kwargs["resume_stamps"] == stamps
        assert kwargs["baseline_files"] == {}
        retained = args[2](requests[0])
        assert retained.body == turns["track_a_capital-7"].response
        assert retained.trace == turns["track_a_capital-7"].trace
        assert retained.retained_request == original_prefix
        assert requests[0].data["evidence_epoch"] == 4
        # Stop before workflow execution: the full completion/replay path has a separate integration test.
        raise RuntimeError("dispatch_captured")

    monkeypatch.setattr(resume, "load_resume", lambda _root: (inputs, config, {}))
    monkeypatch.setattr(resume, "load_completed_turns", lambda _root, **_kwargs: (turns, stamps))
    monkeypatch.setattr(resume, "load_acquisition_turns", lambda _root: [])
    monkeypatch.setattr(resume, "load_retained_acquisitions", lambda _root: ())
    monkeypatch.setattr(research_acquisition, "ResearchAcquisitionExecutor", lambda **_kwargs: object())
    monkeypatch.setattr(acquisition, "run_workflow", capture_dispatch)
    monkeypatch.setattr(baseline, "prepare_baseline", forbidden)
    monkeypatch.setattr("stock_research_llm_orchestrator.research.cli.preflight_research", lambda *_: None)
    monkeypatch.setattr("stock_research_llm_orchestrator.research.cli.invoke_web_research", forbidden)
    assert (
        cli.main(
            [
                "research",
                "run",
                "--agents",
                str(PROFILE),
                "--task",
                str(task_path),
                "--prior",
                str(prior_root),
                "--runs",
                str(tmp_path),
                "--runtime",
                str(tmp_path / "runtime"),
                "--destination",
                "dispatch-only",
                "--resume-from",
                str(source),
                "--resume-completed-turns",
            ]
        )
        != 0
    )
    assert selected == [expected_epoch]
    assert turns["track_a_capital-7"].request == original_prefix
    assert not (tmp_path / "dispatch-only").exists()
