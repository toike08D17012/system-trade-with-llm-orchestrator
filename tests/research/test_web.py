"""Exercise isolated native-Web research, citation provenance, quote calculation and offline replay."""

import json
from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue

from stock_research_llm_orchestrator.agents.configuration import load_agent_configuration
from stock_research_llm_orchestrator.agents.research_provider import WebTurnResult
from stock_research_llm_orchestrator.research.web import SPECIALISTS, run_workflow
from stock_research_llm_orchestrator.research.workflow import ResearchRequest, validate_bundle

from .test_configured import PROFILE, ROOT
from .test_configured import _invoke as configured_response
from .test_workflow import _inputs


INSTRUCTIONS = ROOT / "agent-sources/detailed-analysis/independent-web-v1"


def _invoke(request: ResearchRequest) -> WebTurnResult:
    if request.stage == "translation":
        return WebTurnResult(configured_response(request), b"")
    payload = json.loads(configured_response(request))
    source_id = request.stage + "-web-1"
    payload["research"] = {
        "queries": [request.stage + " issuer financials valuation counterevidence"],
        "sources": [
            {
                "source_id": source_id,
                "url": "https://example.com/" + request.stage,
                "title": "Synthetic original source",
                "supporting_excerpt": "Listed share quote JPY 100.",
                "retrieval_status": "page_read",
                "retrieved_at": None,
            }
        ],
        "unavailable_information": [],
    }
    key = "horizons" if request.stage in SPECIALISTS else "horizon_analyses"
    if request.stage in SPECIALISTS or request.stage.endswith("aggregator"):
        for horizon in payload[key]:
            for claim in horizon["claims"]:
                claim["web_source_ids"] = [source_id]
            if request.stage in SPECIALISTS:
                for scenario in horizon["scenarios"]:
                    scenario["web_source_ids"] = [source_id]
    if request.stage == "orchestrator":
        payload["web_observed_price"] = None
        for conclusion in payload["conclusions"]:
            for scenario in conclusion["scenarios"]:
                scenario["web_source_ids"] = [source_id]
    trace = json.dumps({"type": "item.started", "item": {"id": request.stage, "type": "web_search"}}).encode() + b"\n"
    return WebTurnResult(json.dumps(payload).encode(), trace)


def test_specialists_search_independently_and_keep_web_sources_separate() -> None:
    """No peer search content enters initial research; later stages retain source provenance."""
    requests: list[ResearchRequest] = []

    def invoke(request: ResearchRequest) -> WebTurnResult:
        requests.append(request)
        return _invoke(request)

    files = run_workflow(_inputs(), load_agent_configuration(PROFILE), invoke, INSTRUCTIONS, mode="replay")
    for request in requests[:4]:
        assert "previous_web_sources" not in request.data
        assert "specialists" not in request.data
    assert set(cast(dict[str, JsonValue], requests[4].data["previous_web_sources"])) == {
        "track_a_capital-web-1",
        "track_a_income-web-1",
    }
    assert set(cast(dict[str, JsonValue], requests[5].data["previous_web_sources"])) == {
        "track_b_capital-web-1",
        "track_b_income-web-1",
    }
    assert len(requests[-1].data) <= 30
    manifest = json.loads(files["manifest.json"])
    assert manifest["format_version"] == 4 and manifest["analysis_completed"] is False
    assert manifest["executions"]["track_b_capital"]["native_web_tool_events"] == 1
    assert "initial_evidence_set_reference" in json.loads(files["track_a_capital.json"])
    assert "機械照合" in files["report.md"].decode()
    validate_bundle(files)


@pytest.mark.parametrize("failure", ["no_tool_event", "invented_source", "snippet_as_fact", "unsafe_url"])
def test_unsubstantiated_web_research_is_not_adopted(failure: str) -> None:
    """Search claims in prose are insufficient, and citations must resolve to read pages."""
    seen: list[str] = []

    def invoke(request: ResearchRequest) -> WebTurnResult:
        seen.append(request.stage)
        result = _invoke(request)
        body = json.loads(result.body)
        if failure == "no_tool_event":
            return WebTurnResult(result.body, b'{"type":"agent_message","text":"I searched the Web"}\n')
        if failure == "invented_source":
            body["horizons"][0]["claims"][0]["web_source_ids"] = ["absent"]
        elif failure == "snippet_as_fact":
            body["research"]["sources"][0]["retrieval_status"] = "search_result_only"
        else:
            body["research"]["sources"][0]["url"] = "javascript:alert(1)"
        return WebTurnResult(json.dumps(body).encode(), result.trace)

    with pytest.raises(ValueError):
        run_workflow(_inputs(), load_agent_configuration(PROFILE), invoke, INSTRUCTIONS, mode="replay")
    assert seen == ["track_a_capital"]


@pytest.mark.parametrize("specialist_disagrees", [False, True])
def test_native_web_quote_can_support_conditional_returns(specialist_disagrees: bool) -> None:
    """A separately labeled agent-reported price enables calculation without inventing common evidence IDs."""

    def invoke(request: ResearchRequest) -> WebTurnResult:
        result = _invoke(request)
        if request.stage == "translation":
            return result
        body = json.loads(result.body)
        if request.stage in SPECIALISTS:
            for horizon in body["horizons"]:
                horizon.update(evaluability="evaluable", missing_information=[])
                for scenario in horizon["scenarios"]:
                    scenario.update(
                        terminal_price="140",
                        cumulative_dividends="6",
                        evidence_ids=[],
                        missing_information=[],
                        per_share_basis="current listed-share basis",
                    )
        if specialist_disagrees and request.stage == "track_b_capital":
            for horizon in body["horizons"]:
                horizon.update(evaluability="not_evaluable", missing_information=["Uncertain valuation."])
                for scenario in horizon["scenarios"]:
                    scenario.update(terminal_price=None, cumulative_dividends=None)
        if request.stage.endswith("aggregator") or request.stage == "orchestrator":
            key = "horizon_results" if request.stage == "orchestrator" else "horizon_analyses"
            for horizon in body[key]:
                horizon.update(
                    evaluability="evaluable",
                    assessment={
                        "status": "conditional",
                        "confidence": "low",
                        "rationale": "Synthetic conditional fixture.",
                        "condition_or_research_needs": ["Verify data."],
                    },
                )
        if request.stage == "orchestrator":
            body["web_observed_price"] = {
                "price": "100",
                "currency": "JPY",
                "observed_on": "2026-10-29",
                "per_share_basis": "current listed-share basis",
                "source_id": "orchestrator-web-1",
                "quote_excerpt": "Listed share quote JPY 100.",
            }
            for conclusion in body["conclusions"]:
                conclusion.update(primary_objective="capital_gain", decision="candidate", missing_information=[])
                for scenario in conclusion["scenarios"]:
                    scenario.update(
                        terminal_price="140",
                        cumulative_dividends="6",
                        evidence_ids=[],
                        missing_information=[],
                        per_share_basis="current listed-share basis",
                    )
        return WebTurnResult(json.dumps(body).encode(), result.trace)

    files = run_workflow(_inputs(), load_agent_configuration(PROFILE), invoke, INSTRUCTIONS, mode="replay")
    returns = json.loads(files["calculated-returns.json"])
    assert returns["medium_term"][1]["total_return"] == "0.46"
    assert returns["medium_term"][1]["entry_price"]["provenance"] == "agent_reported_native_web_not_machine_verified"
    assert "46.0%" in files["report.md"].decode()
    assert "100 JPY" in files["report.md"].decode()
    files["provider-traces/track_a_capital.jsonl"] += b" changed"
    with pytest.raises(ValueError):
        validate_bundle(files)


def test_public_legacy_web_replay_preserves_native_trace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Archived format4 replay stays offline after live dispatch advances to format5."""
    from stock_research_llm_orchestrator import cli

    files = run_workflow(_inputs(), load_agent_configuration(PROFILE), _invoke, INSTRUCTIONS, mode="replay")
    original = tmp_path / "original"
    for name, body in files.items():
        path = original / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    monkeypatch.setattr("stock_research_llm_orchestrator.research.cli.preflight_research", lambda *_: None)
    monkeypatch.setattr("stock_research_llm_orchestrator.research.cli.validate_transfer", lambda *_: None)
    monkeypatch.setattr("stock_research_llm_orchestrator.research.cli.invoke_web_research", _invoke)
    arguments = [
        "--agents",
        str(PROFILE),
        "--task",
        str(original / "input/parent-task.json"),
        "--prior",
        str(original / "input/prior"),
        "--runs",
        str(tmp_path),
    ]
    live = original
    monkeypatch.setattr(
        "stock_research_llm_orchestrator.research.cli.invoke_web_research",
        lambda *_: pytest.fail("offline replay must not invoke a provider"),
    )
    monkeypatch.setattr(
        "stock_research_llm_orchestrator.research.cli.preflight_research",
        lambda *_: pytest.fail("offline replay must not authenticate"),
    )
    assert (
        cli.main(
            [
                "research",
                "replay",
                *arguments,
                "--destination",
                "replay",
                "--responses",
                str(live),
                "--at",
                json.loads((live / "input/evidence-set.json").read_bytes())["evidence_frozen_at"],
            ]
        )
        == 0
    )
    assert (tmp_path / "replay/orchestrator.json").read_bytes() == (live / "orchestrator.json").read_bytes()
    assert cli.main(["research", "validate", "--bundle", str(tmp_path / "replay")]) == 0
