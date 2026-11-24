"""Integration-directed send-back, effort override and conditional second opinion (v6)."""

import json
import subprocess

import pytest

from stock_research_llm_orchestrator.agents.configuration import load_agent_configuration
from stock_research_llm_orchestrator.agents.research_provider import WebTurnResult
from stock_research_llm_orchestrator.research.acquisition import replay_saved_bundle, run_workflow, validate_bundle
from stock_research_llm_orchestrator.research.configured import SPECIALISTS
from stock_research_llm_orchestrator.research.directed import (
    DIRECTED_PROFILE,
    RECOVERY_PROFILE,
    DirectedMainTurn,
    ReviewResponse,
    invoke_antigravity,
)
from stock_research_llm_orchestrator.research.workflow import ResearchRequest

from .test_acquisition import INSTRUCTIONS, _acquire, _acquisition_turn, _final
from .test_configured import PROFILE
from .test_document_navigation import _summary
from .test_workflow import _inputs


RERUN = {"roles": ["track_b_capital"], "effort": "medium", "shared_evidence_ids": ["acquired-filing"], "reason": "x"}


def _run(invoke, second_opinion=None, execution_profile=DIRECTED_PROFILE) -> dict[str, bytes]:  # type: ignore[no-untyped-def]
    return run_workflow(
        _inputs(),
        load_agent_configuration(PROFILE),
        invoke,
        INSTRUCTIONS,
        mode="replay",
        acquire=_acquire,
        execution_profile=execution_profile,
        reassessment_from_epoch=2,
        stable_evidence_from_epoch=1,
        batch_common_updates_from_epoch=1,
        second_opinion=second_opinion,
    )


def _responder(  # type: ignore[no-untyped-def]
    main_rerun: dict | None, decisions: list[str], calls: list[ResearchRequest], review_passed: bool = False
):
    acquired: set[str] = set()

    def invoke(request: ResearchRequest) -> WebTurnResult:
        calls.append(request)
        if request.stage.startswith("document_summary-"):
            return _summary(request)
        if request.stage == SPECIALISTS[0] and request.stage not in acquired:
            acquired.add(request.stage)
            return _acquisition_turn()
        if request.response_model is ReviewResponse:
            decision = decisions.pop(0)
            body = {"decision": decision, "reason": "because", "rerun_request": RERUN if decision == "rerun" else None}
            return WebTurnResult(json.dumps(body).encode(), b"")
        turn = _final(request)
        if request.stage == "primary_reviewer" and not review_passed:
            body = json.loads(turn.body)
            body["payload"].update(overall_status="rework", review_passed=False)
            body["payload"]["completion_gate"]["finding_processing_complete"] = False
            return WebTurnResult(json.dumps(body).encode(), turn.trace)
        if request.response_model is DirectedMainTurn:
            body = json.loads(turn.body)
            body["rerun_request"] = main_rerun
            return WebTurnResult(json.dumps(body).encode(), turn.trace)
        return turn

    return invoke


def _check_replay(files: dict[str, bytes]) -> None:
    validate_bundle(files)
    assert replay_saved_bundle(files, _inputs(), load_agent_configuration(PROFILE)) == files


def test_acquisition_does_not_restart_and_accept_finishes_in_one_epoch() -> None:
    """Evidence stays with its role, effort stays low, and a clean passing review skips the decision turn."""
    calls: list[ResearchRequest] = []
    files = _run(_responder(None, [], calls, review_passed=True))
    assert json.loads(files["manifest.json"])["epochs"] == 1
    assert not any(r.stage.startswith("review-response-") for r in calls)
    assert json.loads(files["incremental-decisions.json"])[-1]["review_response"] == "skipped_review_passed"
    assert all(r.agent is None or r.agent.reasoning.effort == "low" for r in calls)
    assert "second-opinion/status.json" not in files
    _check_replay(files)


@pytest.mark.parametrize(("main_rerun", "decisions"), [(RERUN, ["rerun", "accept"]), (None, ["rerun", "rerun"])])
def test_single_send_back_reruns_only_named_specialist_with_chosen_effort(
    main_rerun: dict | None, decisions: list[str]
) -> None:
    """Synthesis or review send-back is used once; a second request is ignored and recorded."""
    calls: list[ResearchRequest] = []
    files = _run(_responder(main_rerun, decisions, calls))
    assert json.loads(files["manifest.json"])["epochs"] == 2
    second = [r for r in calls if r.stage in SPECIALISTS and r.data.get("evidence_epoch") == 2]
    assert {r.stage for r in second} == {"track_b_capital"}
    assert all(r.agent is not None and r.agent.reasoning.effort == "medium" for r in second)
    assert "acquired-filing" in json.dumps(second[0].data)
    log = json.loads(files["incremental-decisions.json"])
    assert sum("send_back" in item for item in log) == 1
    assert log[-1].get("send_back_ignored") is True
    _check_replay(files)


@pytest.mark.parametrize("fails", [False, True])
def test_second_opinion_runs_only_on_disagreement_and_failure_is_not_fatal(fails: bool) -> None:
    """Antigravity output is a reference section; failure is recorded as unavailable."""

    def opinion(prompt: str) -> bytes:
        assert "because" in prompt
        if fails:
            raise RuntimeError("down")
        return "第三者の見解".encode()

    files = _run(_responder(None, ["second_opinion"], []), opinion)
    status = json.loads(files["second-opinion/status.json"])["status"]
    assert status == ("unavailable" if fails else "completed")
    assert ("未取得" if fails else "第三者の見解") in files["report.md"].decode()
    _check_replay(files)


def test_antigravity_outer_timeout_still_publishes_and_replays(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real launcher adapter turns the external timeout into an unavailable opinion."""

    def expire(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired("agy", 1800)

    monkeypatch.setattr(subprocess, "run", expire)
    files = _run(_responder(None, ["second_opinion"], []), invoke_antigravity)
    assert json.loads(files["second-opinion/status.json"])["status"] == "unavailable"
    assert "未取得" in files["report.md"].decode()
    _check_replay(files)


def test_standard_price_is_preferred_and_web_discrepancy_is_retained(monkeypatch: pytest.MonkeyPatch) -> None:
    """The standard observation drives v7 while the same saved v6 response uses the Web price."""
    standard = {
        "price": "101.0",
        "currency": "JPY",
        "on": "2026-10-29",
        "per_share_basis": "current listed-share basis",
        "provenance": "machine_parsed_native_yfinance_observation",
    }
    monkeypatch.setattr(
        "stock_research_llm_orchestrator.research.acquisition.standard_price_observation", lambda *_: standard
    )

    def invoke(request: ResearchRequest) -> WebTurnResult:
        turn = _summary(request) if request.stage.startswith("document_summary-") else _final(request)
        if request.stage == "translation" or request.stage.startswith("document_summary-"):
            return turn
        body = json.loads(turn.body)
        payload = body["payload"]
        for result in payload.get("horizons", []) + payload.get("conclusions", []):
            for scenario in result["scenarios"]:
                scenario["per_share_basis"] = standard["per_share_basis"]
        if request.stage == "orchestrator":
            payload["web_observed_price"] = {
                "price": "100",
                "currency": "JPY",
                "observed_on": "2026-10-29",
                "per_share_basis": standard["per_share_basis"],
                "source_id": "orchestrator-web-1",
                "quote_excerpt": "Listed share quote JPY 100.",
            }
        return WebTurnResult(json.dumps(body).encode(), turn.trace)

    old = _run(invoke)
    assert "100 JPY" in old["report.md"].decode()
    _check_replay(old)
    new = _run(invoke, execution_profile=RECOVERY_PROFILE)
    assert "101.0 JPY" in new["report.md"].decode()
    difference = json.loads(new["price-source-discrepancy.json"])
    assert difference["used"] == standard
    assert difference["reported_web"]["price"] == "100"
    _check_replay(new)
