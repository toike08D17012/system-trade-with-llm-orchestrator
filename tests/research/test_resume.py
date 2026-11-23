"""Resume reuses validated baseline originals while keeping new analysis calls explicit."""

import json
from pathlib import Path
from typing import Any

import pytest

from stock_research_llm_orchestrator import cli
from stock_research_llm_orchestrator.agents.configuration import load_agent_configuration
from stock_research_llm_orchestrator.agents.research_provider import WebTurnResult
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.agent_configuration import AgentConfigurationV1
from stock_research_llm_orchestrator.errors import ExitCode
from stock_research_llm_orchestrator.preparation import research_acquisition
from stock_research_llm_orchestrator.preparation.research_draft import DraftInput, DraftOutput, build_bundle
from stock_research_llm_orchestrator.research import baseline
from stock_research_llm_orchestrator.research.input import serialize
from stock_research_llm_orchestrator.research.resume import load_resume
from stock_research_llm_orchestrator.research.workflow import ResearchRequest

from .test_acquisition import _final
from .test_baseline import _acquire, _discovery
from .test_configured import PROFILE
from .test_workflow import STAMP, _inputs, _source


def _save(root: Path, files: dict[str, bytes]) -> None:
    for name, body in files.items():
        path = root / name
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.write_bytes(body)


def _checkpoint(tmp_path: Path) -> tuple[Path, dict[str, bytes], AgentConfigurationV1, dict[str, bytes]]:
    inputs = _inputs()
    config = load_agent_configuration(PROFILE)
    prepared = baseline.prepare_baseline(inputs, config, _acquire, lambda _request: _discovery(()), at=STAMP)
    root = tmp_path / "checkpoint"
    root.mkdir(mode=0o700)
    _save(root / "input", inputs)
    _save(root / "baseline", prepared)
    (root / "agent-configuration.json").write_bytes(serialize(config))
    return root, inputs, config, prepared


def test_load_resume_keeps_original_input_configuration_and_acquired_baseline(tmp_path: Path) -> None:
    """Resumption loads validated saved bytes without acquisition or provider calls."""
    root, inputs, config, prepared = _checkpoint(tmp_path)
    saved_inputs, saved_config, saved_baseline = load_resume(root)
    assert saved_inputs == inputs
    assert serialize(saved_config) == serialize(config)
    assert saved_baseline == prepared
    assert json.loads(saved_inputs["evidence-set.json"])["evidence_frozen_at"] == STAMP


@pytest.mark.parametrize("target", ["input", "original", "configuration"])
def test_resume_rejects_changed_saved_bindings(tmp_path: Path, target: str) -> None:
    """Input provenance, source bytes and the configured preparation agent remain bound."""
    root, _inputs_bytes, _config, _prepared = _checkpoint(tmp_path)
    if target == "input":
        (root / "input/common-input.json").write_bytes(b"[]")
    elif target == "original":
        path = next((root / "baseline/acquisitions").rglob("jpx.xlsx"))
        path.write_bytes(path.read_bytes() + b" changed source")
    else:
        path = root / "agent-configuration.json"
        value = json.loads(path.read_bytes())
        value["agents"]["gpt"]["model"] = "different-model"
        path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError):
        load_resume(root)


def test_public_resume_skips_baseline_retrieval_and_starts_new_analysis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The public resume option reuses saved originals but invokes actual research turns anew."""
    checkpoint, inputs, _config, _prepared = _checkpoint(tmp_path)
    task, prior = _source()
    task_path = tmp_path / "task.json"
    task_path.write_bytes(task)
    _save(tmp_path / "prior", prior)
    stages: list[str] = []

    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("Resume must not rerun baseline discovery or retrieval")

    def executor_factory(**_kwargs: Any) -> object:
        return forbidden

    def invoke(request: ResearchRequest) -> WebTurnResult:
        assert request.stage != "baseline_discovery"
        stages.append(request.stage)
        turn = _final(request)
        if request.stage == "translation":
            return turn
        body = json.loads(turn.body)
        observed = request.data.get("observed_price")
        if isinstance(observed, dict):
            for key in ("horizons", "conclusions"):
                for horizon in body["payload"].get(key, []):
                    for scenario in horizon["scenarios"]:
                        scenario["per_share_basis"] = observed["per_share_basis"]
        return WebTurnResult(json.dumps(body).encode(), turn.trace)

    monkeypatch.setattr(baseline, "prepare_baseline", forbidden)
    monkeypatch.setattr(research_acquisition, "ResearchAcquisitionExecutor", executor_factory)
    monkeypatch.setattr("stock_research_llm_orchestrator.research.cli.preflight_research", lambda *_args: None)
    monkeypatch.setattr("stock_research_llm_orchestrator.research.cli.invoke_web_research", invoke)
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
                str(tmp_path / "prior"),
                "--runs",
                str(tmp_path),
                "--destination",
                "resumed",
                "--runtime",
                str(tmp_path / "runtime"),
                "--resume-from",
                str(checkpoint),
            ]
        )
        == 0
    )
    output = tmp_path / "resumed"
    assert (output / "report.md").is_file()
    assert len(stages) == 9
    assert (output / "input/evidence-set.json").read_bytes() == inputs["evidence-set.json"]


@pytest.mark.parametrize("change", ["task", "prior", "configuration"])
def test_public_resume_rejects_changed_requested_inputs_before_provider_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    """Matching filenames alone cannot authorize reuse under different analysis inputs."""
    checkpoint, _inputs_bytes, config, _prepared = _checkpoint(tmp_path)
    task, prior = _source()
    profile = PROFILE
    if change == "task":
        payload = json.loads(task)
        payload["task_id"] = "different-task"
        task = json.dumps(payload).encode()
    elif change == "prior":
        supplied = DraftInput.model_validate_json(prior["input.json"])
        evidence = supplied.evidence[0].model_copy(update={"text": "Different supplied public disclosure text."})
        prior = build_bundle(
            supplied.model_copy(update={"evidence": (evidence,)}),
            DraftOutput.model_validate_json(prior["draft.json"]),
        )
    else:
        payload = config.model_dump(mode="json")
        payload["agents"]["gpt"]["model"] = "different-model"
        profile = tmp_path / "different-agent-profile.json"
        profile.write_text(json.dumps(payload), encoding="utf-8")
    task_path = tmp_path / "task.json"
    task_path.write_bytes(task)
    _save(tmp_path / "prior", prior)

    def forbidden(*_args: object, **_kwargs: object) -> None:
        pytest.fail("Mismatched resume must fail before authentication, discovery, or research calls")

    monkeypatch.setattr("stock_research_llm_orchestrator.research.cli.preflight_research", forbidden)
    monkeypatch.setattr("stock_research_llm_orchestrator.research.cli.invoke_web_research", forbidden)
    assert (
        cli.main(
            [
                "research",
                "run",
                "--agents",
                str(profile),
                "--task",
                str(task_path),
                "--prior",
                str(tmp_path / "prior"),
                "--runs",
                str(tmp_path),
                "--destination",
                "rejected",
                "--runtime",
                str(tmp_path / "runtime"),
                "--resume-from",
                str(checkpoint),
            ]
        )
        == ExitCode.INVALID_CONFIGURATION
    )
    assert not (tmp_path / "rejected").exists()
