"""Verify objective isolation, evidence constraints, calculations and reproducible bundles."""

import json
from pathlib import Path

import pytest
from pydantic import JsonValue

from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.objectives import ScenarioV1, SpecialistBody
from stock_research_llm_orchestrator.research.objectives import SPECIALISTS, STAGES, run_workflow
from stock_research_llm_orchestrator.research.scenarios import total_return
from stock_research_llm_orchestrator.research.workflow import ResearchRequest, validate_bundle

from .test_workflow import _inputs


ROOT = Path(__file__).parents[2]
INSTRUCTIONS = ROOT / "agent-sources/detailed-analysis/objectives-v1"
FIXTURES = ROOT / "tests/fixtures/research/objectives"


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


def test_objective_specialists_are_isolated_and_review_sees_all_evidence() -> None:
    """Every objective is independent and only same-family results reach aggregation."""
    requests = []

    def invoke(request: ResearchRequest) -> bytes:
        requests.append(request)
        return _invoke(request)

    files = run_workflow(_inputs(), invoke, INSTRUCTIONS, mode="replay")
    assert tuple(request.stage for request in requests) == STAGES
    for request in requests[:4]:
        assert "specialists" not in request.data and "workers" not in request.data
        assert "Fixture only" not in json.dumps(request.data)
    assert requests[0].data["objective"] == requests[2].data["objective"] == "capital_gain"
    assert requests[1].data["objective"] == requests[3].data["objective"] == "income_gain"
    for index, provider in ((4, "codex"), (5, "claude")):
        family_specialists = requests[index].data["specialists"]
        assert isinstance(family_specialists, dict)
        assert set(family_specialists) == {f"{provider}_capital", f"{provider}_income"}
    assert isinstance(requests[7].data["specialists"], dict)
    assert set(requests[7].data["specialists"]) == set(SPECIALISTS)
    assert "investment_conclusions" in requests[7].data and "calculated_returns" in requests[7].data
    assert requests[7].provider == "claude"
    validate_bundle(files)
    manifest = json.loads(files["manifest.json"])
    assert manifest["format_version"] == 2
    assert manifest["analysis_completed"] is False
    report = files["report.md"].decode()
    assert "判断保留" in report and "キャピタルゲイン分析" in report and "一次レビュー" in report
    assert json.loads(files["calculated-returns.json"])["medium_term"][0]["status"] == "not_calculable"


@pytest.mark.parametrize(
    "name",
    [
        "requests/codex_worker.json",
        "responses/codex_income.json",
        "investment_conclusions.json",
        "calculated-returns.json",
        "report.md",
    ],
)
def test_objective_bundle_rejects_tampering(name: str) -> None:
    """Requests, adopted conclusions, calculations and report are reconstructed exactly."""
    files = run_workflow(_inputs(), _invoke, INSTRUCTIONS, mode="replay")
    files[name] += b" "
    with pytest.raises(ValueError):
        validate_bundle(files)


@pytest.mark.parametrize("change", ["evidence", "duplicate", "numeric", "order"])
def test_invalid_specialist_output_stops_before_other_roles(change: str) -> None:
    """Invalid evidence or unsupported forecasts never reach a peer or aggregator."""
    seen = []

    def invoke(request: ResearchRequest) -> bytes:
        seen.append(request.stage)
        payload = json.loads(_invoke(request))
        horizon = payload["horizons"][0]
        if change == "evidence":
            horizon["claims"][0]["evidence_ids"] = ["invented"]
        elif change == "duplicate":
            payload["horizons"][1]["claims"][0]["claim_id"] = horizon["claims"][0]["claim_id"]
        elif change == "numeric":
            horizon["scenarios"][0]["terminal_price"] = "1500"
        else:
            horizon["scenarios"].reverse()
        return json.dumps(payload).encode()

    with pytest.raises(ValueError):
        run_workflow(_inputs(), invoke, INSTRUCTIONS, mode="replay")
    assert seen == ["codex_capital"]


def test_missing_price_prevents_a_purchase_conclusion() -> None:
    """Even coherent qualitative stories cannot establish value at an unknown price."""

    def invoke(request: ResearchRequest) -> bytes:
        payload = json.loads(_invoke(request))
        if request.stage == "orchestrator":
            payload["conclusions"][0].update(decision="candidate", primary_objective="capital_gain")
        return json.dumps(payload).encode()

    with pytest.raises(ValueError):
        run_workflow(_inputs(), invoke, INSTRUCTIONS, mode="replay")


def test_failed_specialist_does_not_fall_back_to_general_analysis() -> None:
    """Provider failure propagates without generating replacement evidence or findings."""
    seen = []

    def fail(request: ResearchRequest) -> bytes:
        seen.append(request.stage)
        raise RuntimeError("synthetic provider failure")

    with pytest.raises(RuntimeError):
        run_workflow(_inputs(), fail, INSTRUCTIONS, mode="replay")
    assert seen == ["codex_capital"]


@pytest.mark.parametrize(("terminal", "dividends", "expected"), [("140", "6", "0.46"), ("80", "0", "-0.2")])
def test_total_return_adds_income_once(terminal: str, dividends: str, expected: str) -> None:
    """Capital changes and cumulative dividends combine without reinvestment or annualization."""
    payload = json.loads((FIXTURES / "codex_capital.json").read_bytes())["horizons"][0]["scenarios"][0]
    payload.update(
        terminal_price=terminal,
        cumulative_dividends=dividends,
        per_share_basis="retained-price-basis",
        missing_information=[],
    )
    scenario = ScenarioV1.model_validate_json(json.dumps(payload))
    result = total_return(scenario, {"price": "100", "currency": "JPY", "per_share_basis": "retained-price-basis"})
    assert result["total_return"] == expected
    assert total_return(scenario, None)["status"] == "not_calculable"
    with pytest.raises(ValueError, match="basis_mismatch"):
        total_return(scenario, {"price": "100", "currency": "USD", "per_share_basis": "retained-price-basis"})


@pytest.mark.parametrize("invalid", ["NaN", "Infinity", "-1", "not-a-number"])
def test_nonfinite_or_negative_assumptions_are_rejected(invalid: str) -> None:
    """Untrusted numerical assumptions cannot contaminate a valid-looking return."""
    payload = json.loads((FIXTURES / "codex_capital.json").read_bytes())["horizons"][0]["scenarios"][0]
    payload["terminal_price"] = invalid
    with pytest.raises(ValueError):
        total_return(ScenarioV1.model_validate_json(json.dumps(payload)), None)


def test_specialist_contract_requires_both_horizons() -> None:
    """A missing horizon cannot become a completed specialty."""
    payload = json.loads((FIXTURES / "codex_capital.json").read_bytes())
    payload["horizons"].pop()
    with pytest.raises(ValueError):
        SpecialistBody.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize("purpose", ["capital_gain", "income_gain"])
def test_evaluable_stories_produce_conditional_returns_and_candidates(
    purpose: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both objectives can be primary without adding the same dividends twice."""
    price = {"price": "100", "currency": "JPY", "per_share_basis": "retained-price-basis"}
    monkeypatch.setattr("stock_research_llm_orchestrator.research.objectives.observed_price", lambda _: price)

    def invoke(request: ResearchRequest) -> bytes:
        if request.stage == "translation":
            return _invoke(request)
        payload = json.loads(_invoke(request))
        if request.stage in SPECIALISTS:
            for horizon in payload["horizons"]:
                horizon.update(evaluability="evaluable", missing_information=[])
                for scenario in horizon["scenarios"]:
                    scenario.update(
                        terminal_price="140",
                        cumulative_dividends="6",
                        per_share_basis="retained-price-basis",
                        missing_information=[],
                    )
        if request.stage in {"codex_worker", "claude_worker", "orchestrator"}:
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

    files = run_workflow(_inputs(), invoke, INSTRUCTIONS, mode="replay")
    validate_bundle(files)
    result = json.loads(files["calculated-returns.json"])
    assert result["medium_term"][1]["total_return"] == "0.46"
    assert "購入候補" in files["report.md"].decode()


@pytest.mark.parametrize(
    ("pointer", "invalid"), [("/conclusions/0/story", False), ("", False), ("/conclusions/99", True)]
)
def test_review_can_target_objective_conclusion_and_rejects_invalid_pointers(pointer: str, invalid: bool) -> None:
    """Open material findings survive and can point directly to the new conclusion artifact."""

    def invoke(request: ResearchRequest) -> bytes:
        if request.stage != "primary_reviewer":
            return _invoke(request)
        payload = json.loads(_invoke(request))
        payload.update(overall_status="rework", review_passed=False)
        payload["completion_gate"]["finding_processing_complete"] = False
        payload["findings"] = [
            {
                "finding_id": "objective-finding",
                "target_artifact_reference": request.data["conclusion_reference"],
                "target_claim_ids": ["codex_capital-medium_term"],
                "json_pointer": pointer,
                "horizon": "medium_term",
                "summary": "Story requires additional evidence.",
                "detail": "Cash allocation needs verification.",
                "primary_classification": "insufficient_evidence",
                "secondary_classifications": [],
                "classification_reason": "The excerpt is insufficient.",
                "classification_pending": None,
                "severity": "high",
                "severity_reason": "Decision may change.",
                "evidence_ids": ["ir-excerpt"],
                "policy_references": [request.data["policy_reference"]],
                "schema_references": [],
                "materiality": {"impacts": [], "is_material": False},
                "expected_resolution": "Verify cash flows.",
                "lifecycle": "open",
                "unresolved_retention_reason": None,
            }
        ]
        return json.dumps(payload).encode()

    if invalid:
        with pytest.raises(ValueError, match="pointer_invalid"):
            run_workflow(_inputs(), invoke, INSTRUCTIONS, mode="replay")
    else:
        files = run_workflow(_inputs(), invoke, INSTRUCTIONS, mode="replay")
        manifest = json.loads(files["manifest.json"])
        assert manifest["review_passed"] is False
        assert manifest["unresolved_finding_ids"] == ["objective-finding"]
        assert "objective-finding" in files["report.md"].decode()
        assert "未採用" in files["report.md"].decode()


def test_observed_price_requires_unambiguous_quality_passed_snapshot() -> None:
    """Price quality and missing quotes are enforced before scenario analysis."""
    from stock_research_llm_orchestrator.research.scenarios import observed_price

    def excerpt(quality: bool, close: str | None) -> dict[str, JsonValue]:
        return {
            "source_id": "yfinance",
            "evidence_id": "price-fixture",
            "text": json.dumps(
                {
                    "quality_passed": quality,
                    "currency": "JPY",
                    "last_observed_row": {"on": "2026-10-29", "close": close},
                }
            ),
        }

    assert observed_price([excerpt(False, "100")]) is None
    assert observed_price([excerpt(True, None)]) is None
    assert observed_price([excerpt(True, "100"), excerpt(True, "101")]) is None
    result = observed_price([excerpt(True, "100")])
    assert result is not None and result["price"] == "100"


def test_unknown_bundle_version_is_rejected() -> None:
    """Unknown versions cannot silently fall back to the old analysis path."""
    with pytest.raises(ValueError, match="version_invalid"):
        validate_bundle({"manifest.json": b'{"format_version": 99}'})


def test_public_cli_defaults_to_objective_workflow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Default replay publishes a format-2 bundle and validates without any live provider."""
    from stock_research_llm_orchestrator import cli

    from .test_workflow import STAMP, _source

    files = run_workflow(_inputs(), _invoke, INSTRUCTIONS, mode="replay")
    task, prior = _source()
    (tmp_path / "task.json").write_bytes(task)
    prior_root = tmp_path / "prior"
    prior_root.mkdir()
    for name, body in prior.items():
        (prior_root / name).write_bytes(body)
    responses = tmp_path / "responses"
    responses.mkdir()
    for name, body in files.items():
        if name.startswith("responses/"):
            (responses / Path(name).name).write_bytes(body)
    monkeypatch.setattr(
        "stock_research_llm_orchestrator.research.cli.invoke_research",
        lambda _: pytest.fail("live provider must not run"),
    )
    args = [
        "research",
        "replay",
        "--task",
        str(tmp_path / "task.json"),
        "--prior",
        str(prior_root),
        "--runs",
        str(tmp_path),
        "--destination",
        "result",
        "--responses",
        str(responses),
        "--at",
        STAMP,
    ]
    assert cli.main(args) == 0
    assert cli.main(["research", "validate", "--bundle", str(tmp_path / "result")]) == 0
    assert json.loads((tmp_path / "result/manifest.json").read_bytes())["format_version"] == 2
    assert (tmp_path / "result/report.md").read_bytes() == files["report.md"]
