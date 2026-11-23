"""Validate configurable role isolation, selected-provider preflight and exact replay."""

import json
import subprocess
from pathlib import Path
from typing import Literal, cast

import pytest
from pydantic import JsonValue

from stock_research_llm_orchestrator.agents.configuration import load_agent_configuration
from stock_research_llm_orchestrator.agents.research_provider import invoke_research, preflight_research
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.agent_configuration import AgentConfigurationV1
from stock_research_llm_orchestrator.research.configured import STAGES, replay_saved_bundle, run_workflow
from stock_research_llm_orchestrator.research.workflow import ReportTranslation, ResearchRequest, validate_bundle

from .test_workflow import _inputs


ROOT = Path(__file__).parents[2]
PROFILE = ROOT / "config/agent-configurations/gpt-only/v1.yaml"
INSTRUCTIONS = ROOT / "agent-sources/detailed-analysis/configured-v1"
FIXTURES = ROOT / "tests/fixtures/research/configured"


def _invoke(request: ResearchRequest) -> bytes:
    if request.stage == "translation":
        return json.dumps(
            {
                "translations": [
                    {"key": key, "text": "検証用の日本語訳。" + str(value)} for key, value in request.data.items()
                ]
            }
        ).encode()
    return (FIXTURES / f"{request.stage}.json").read_bytes()


def test_all_gpt_roles_are_isolated_and_never_labeled_claude() -> None:
    """One shared agent definition still creates nine separate, correctly bound turns."""
    seen = []

    def invoke(request: ResearchRequest) -> bytes:
        seen.append(request)
        return _invoke(request)

    files = run_workflow(_inputs(), load_agent_configuration(PROFILE), invoke, INSTRUCTIONS, mode="replay")
    assert tuple(request.stage for request in seen) == STAGES
    assert all(request.provider == "codex" and request.agent is not None for request in seen)
    assert all(request.agent is not None and request.agent.reasoning.effort == "medium" for request in seen)
    for request in seen[:4]:
        assert "specialists" not in request.data and "summaries" not in request.data
    assert set(cast(dict[str, JsonValue], seen[4].data["specialists"])) == {"track_a_capital", "track_a_income"}
    assert set(cast(dict[str, JsonValue], seen[5].data["specialists"])) == {"track_b_capital", "track_b_income"}
    manifest = json.loads(files["manifest.json"])
    assert manifest["format_version"] == 3 and manifest["analysis_completed"] is False
    assert manifest["executions"]["primary_reviewer"]["reported_model"] is None
    report = files["report.md"].decode()
    assert "同一系統" in report and "判断保留" in report and "Claude" not in report
    validate_bundle(files)


@pytest.mark.parametrize(
    "filename",
    [
        "agent-configuration.json",
        "requests/primary_reviewer.json",
        "track_a_capital.json",
        "responses/orchestrator.json",
        "calculated-returns.json",
        "manifest.json",
        "report.md",
    ],
)
def test_configured_bundle_rejects_changed_bytes(filename: str) -> None:
    """Saved configuration, metadata, model requests and display remain immutable."""
    files = run_workflow(_inputs(), load_agent_configuration(PROFILE), _invoke, INSTRUCTIONS, mode="replay")
    files[filename] += b" "
    with pytest.raises(ValueError):
        validate_bundle(files)


@pytest.mark.parametrize(
    "mutation", ["missing_role", "unknown_agent", "unknown_adapter", "invalid_effort", "same_family"]
)
def test_invalid_configuration_fails_before_any_provider(mutation: str) -> None:
    """Profiles must resolve fully and satisfy the selected review policy offline."""
    payload = load_agent_configuration(PROFILE).model_dump(mode="json")
    if mutation == "missing_role":
        del payload["roles"]["translation"]
    elif mutation == "unknown_agent":
        payload["roles"]["orchestrator"] = "absent"
    elif mutation == "unknown_adapter":
        payload["agents"]["gpt"]["adapter"] = "ollama"
    elif mutation == "invalid_effort":
        payload["agents"]["gpt"]["reasoning"]["effort"] = "impossible"
    else:
        payload["review_policy"]["mode"] = "cross_family_required"
    with pytest.raises(ValueError):
        AgentConfigurationV1.model_validate_json(json.dumps(payload))


def test_cross_family_profile_dispatches_to_selected_agents() -> None:
    """Neutral track names have no influence on their configured transport."""
    profile = load_agent_configuration(ROOT / "config/agent-configurations/cross-family/v1.yaml")
    seen = []

    def invoke(request: ResearchRequest) -> bytes:
        seen.append((request.stage, request.provider))
        return _invoke(request)

    run_workflow(_inputs(), profile, invoke, INSTRUCTIONS, mode="replay")
    assert ("track_b_capital", "claude") in seen and ("primary_reviewer", "claude") in seen


def test_preflight_authenticates_only_used_agents(monkeypatch: pytest.MonkeyPatch) -> None:
    """A GPT-only profile must not require an unused Claude subscription."""
    calls = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(subprocess, "run", run)
    preflight_research(load_agent_configuration(PROFILE))
    assert calls == [["codex", "login", "status"]]


@pytest.mark.parametrize("provider", ["codex", "claude"])
@pytest.mark.parametrize("effort_override", [None, "low", "high"])
def test_model_and_effort_are_explicit_in_restricted_provider_invocation(
    provider: str,
    effort_override: Literal["low", "high"] | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The selected model and reasoning settings survive without exposing prompt text in argv."""
    profile = load_agent_configuration(ROOT / "config/agent-configurations/cross-family/v1.yaml")
    alias = "gpt" if provider == "codex" else "claude"
    assert profile.agents[alias].reasoning.effort == "medium"
    if effort_override is not None:
        payload = profile.model_dump(mode="json")
        payload["agents"][alias]["reasoning"]["effort"] = effort_override
        profile = AgentConfigurationV1.model_validate_json(json.dumps(payload))
    agent = profile.agents[alias]

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        assert command[command.index("--model") + 1] == agent.model
        assert "private input" not in " ".join(command)
        body = {"translations": [{"key": "input", "text": "日本語"}]}
        if provider == "codex":
            assert f'model_reasoning_effort="{agent.reasoning.effort}"' in command
            Path(command[command.index("--output-last-message") + 1]).write_text(json.dumps(body))
            output = b""
        else:
            assert command[command.index("--effort") + 1] == agent.reasoning.effort
            output = json.dumps(
                {"type": "result", "subtype": "success", "is_error": False, "structured_output": body}
            ).encode()
        return subprocess.CompletedProcess(command, 0, output, b"")

    monkeypatch.setattr(subprocess, "run", run)
    selected = cast("Literal['codex', 'claude']", provider)
    invoke_research(
        ResearchRequest(
            "translation", selected, "Translate only.", {"input": "private input"}, ReportTranslation, agent
        )
    )


def test_loader_rejects_ambiguous_yaml(tmp_path: Path) -> None:
    """The runtime loader uses the same strict YAML boundary as configuration validation."""
    path = tmp_path / "agents.yaml"
    path.write_bytes(PROFILE.read_bytes() + b"\nartifact_id: duplicate\n")
    with pytest.raises(ValueError, match="agent_configuration_invalid"):
        load_agent_configuration(path)


@pytest.mark.parametrize("mode", ["live", "replay"])
def test_public_configured_replay_uses_saved_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: Literal["live", "replay"]
) -> None:
    """CLI dispatches format three and validates its snapshot after the source profile is removed."""
    from stock_research_llm_orchestrator import cli

    files = run_workflow(_inputs(), load_agent_configuration(PROFILE), _invoke, INSTRUCTIONS, mode=mode)
    for name, body in files.items():
        path = tmp_path / "original" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    profile = tmp_path / "agents.yaml"
    profile.write_bytes(PROFILE.read_bytes())
    monkeypatch.setattr(
        "stock_research_llm_orchestrator.research.cli.preflight_research",
        lambda *_: pytest.fail("offline replay must not authenticate"),
    )
    original = tmp_path / "original"
    assert (
        cli.main(
            [
                "research",
                "replay",
                "--agents",
                str(profile),
                "--task",
                str(original / "input/parent-task.json"),
                "--prior",
                str(original / "input/prior"),
                "--responses",
                str(original if mode == "live" else original / "responses"),
                "--at",
                json.loads(files["input/evidence-set.json"])["evidence_frozen_at"],
                "--runs",
                str(tmp_path),
                "--destination",
                "result",
            ]
        )
        == 0
    )
    profile.unlink()
    assert cli.main(["research", "validate", "--bundle", str(tmp_path / "result")]) == 0
    assert (tmp_path / "result/orchestrator.json").read_bytes() == files["orchestrator.json"]


def test_configured_review_preserves_open_material_finding() -> None:
    """A real target reference and pointer retain a blocking first-round finding."""

    def invoke(request: ResearchRequest) -> bytes:
        if request.stage != "primary_reviewer":
            return _invoke(request)
        payload = json.loads(_invoke(request))
        payload.update(overall_status="rework", review_passed=False)
        payload["completion_gate"]["finding_processing_complete"] = False
        finding: dict[str, JsonValue] = {
            "finding_id": "finding-evidence-gap",
            "target_artifact_reference": cast(dict[str, JsonValue], request.data["artifact_references"])[
                "orchestrator"
            ],
            "target_claim_ids": [],
            "json_pointer": "/payload/conclusions/0",
            "horizon": "medium_term",
            "summary": "Price evidence is missing.",
            "detail": "Obtain a verified price before adopting a purchase conclusion.",
            "primary_classification": "insufficient_evidence",
            "secondary_classifications": [],
            "classification_reason": "No price was supplied.",
            "classification_pending": None,
            "severity": "high",
            "severity_reason": "Purchase assessment is affected.",
            "evidence_ids": ["ir-excerpt"],
            "policy_references": [request.data["policy_reference"]],
            "schema_references": [],
            "materiality": {"impacts": [], "is_material": False},
            "expected_resolution": "Acquire verified price evidence.",
            "lifecycle": "open",
            "unresolved_retention_reason": None,
        }
        payload["findings"] = [finding]
        return json.dumps(payload).encode()

    config = load_agent_configuration(PROFILE)
    files = run_workflow(_inputs(), config, invoke, INSTRUCTIONS, mode="live")
    manifest = json.loads(files["manifest.json"])
    assert manifest["review_passed"] is False
    assert manifest["unresolved_finding_ids"] == ["finding-evidence-gap"]
    assert "結論は未採用" in files["report.md"].decode()
    assert "重要1件" in files["report.md"].decode()
    replayed = replay_saved_bundle(files, _inputs(), config)
    assert replayed["primary_reviewer.json"] == files["primary_reviewer.json"]
    changed_inputs = _inputs()
    changed_inputs["policy.yaml"] += b" "
    with pytest.raises(ValueError, match="configured_replay_input_mismatch"):
        replay_saved_bundle(files, changed_inputs, config)


@pytest.mark.parametrize("purpose", ["capital_gain", "income_gain"])
def test_configured_candidates_preserve_each_profit_objective(purpose: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Both primary objectives survive configurable routing and deterministic return calculation."""
    price = {"price": "100", "currency": "JPY", "per_share_basis": "retained-price-basis"}
    monkeypatch.setattr("stock_research_llm_orchestrator.research.configured.observed_price", lambda _: price)

    def invoke(request: ResearchRequest) -> bytes:
        if request.stage == "translation":
            return _invoke(request)
        payload = json.loads(_invoke(request))
        if request.stage.endswith(("capital", "income")):
            for horizon in payload["horizons"]:
                horizon.update(evaluability="evaluable", missing_information=[])
                for scenario in horizon["scenarios"]:
                    scenario.update(
                        terminal_price="140",
                        cumulative_dividends="6",
                        per_share_basis="retained-price-basis",
                        missing_information=[],
                    )
        if request.stage.endswith("aggregator") or request.stage == "orchestrator":
            key = "horizon_results" if request.stage == "orchestrator" else "horizon_analyses"
            for horizon in payload[key]:
                horizon.update(
                    evaluability="evaluable",
                    assessment={
                        "status": "conditional",
                        "confidence": "low",
                        "rationale": "Synthetic evaluable scenario.",
                        "condition_or_research_needs": ["Verify assumptions before a human decision."],
                    },
                )
        if request.stage == "orchestrator":
            for conclusion in payload["conclusions"]:
                conclusion.update(primary_objective=purpose, decision="candidate", missing_information=[])
                for scenario in conclusion["scenarios"]:
                    scenario.update(
                        terminal_price="140",
                        cumulative_dividends="6",
                        per_share_basis="retained-price-basis",
                        missing_information=[],
                    )
        return json.dumps(payload).encode()

    files = run_workflow(_inputs(), load_agent_configuration(PROFILE), invoke, INSTRUCTIONS, mode="replay")
    assert json.loads(files["calculated-returns.json"])["medium_term"][1]["total_return"] == "0.46"
    conclusions = json.loads(files["orchestrator.json"])["payload"]["conclusions"]
    assert all(item["primary_objective"] == purpose for item in conclusions)
    assert "購入候補" in files["report.md"].decode()
    assert "46.0%" in files["report.md"].decode()


def test_human_report_translates_only_bounded_decision_fields() -> None:
    """Humans receive one concise decision report rather than the internal analysis corpus."""
    seen: list[ResearchRequest] = []

    def invoke(request: ResearchRequest) -> bytes:
        seen.append(request)
        if request.stage == "orchestrator":
            body = json.loads(_invoke(request))
            for conclusion in body["conclusions"]:
                conclusion["missing_information"] = [f"Missing item {index}." for index in range(100)]
                conclusion["change_conditions"] = [f"Change item {index}." for index in range(100)]
            return json.dumps(body).encode()
        return _invoke(request)

    files = run_workflow(_inputs(), load_agent_configuration(PROFILE), invoke, INSTRUCTIONS, mode="replay")
    translation = seen[-1]
    assert translation.stage == "translation" and len(translation.data) <= 30
    assert all(
        key.startswith(("orchestrator/payload/conclusions/", "primary_reviewer/payload/findings/"))
        for key in translation.data
    )
    report = files["report.md"].decode()
    assert "## track_a_capital" not in report and "英語原文" not in report and "```json" not in report
    assert "ほか98件" in report and "統合分析" in report
    assert "合計収益率" in report and "想定ストーリー" in report
    assert "対象銘柄: 1234 (XTKS)" in report
    assert "difference-long-term-durability" not in report
    assert len(json.loads(files["orchestrator.json"])["payload"]["conclusions"][0]["missing_information"]) == 100
