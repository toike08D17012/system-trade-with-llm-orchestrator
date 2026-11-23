"""Exercise independent analysis, review retention, replay and publication boundaries."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from stock_research_llm_orchestrator import cli
from stock_research_llm_orchestrator.preparation.research_draft import DraftInput, DraftOutput, build_bundle
from stock_research_llm_orchestrator.preparation.storage import publish_preparation
from stock_research_llm_orchestrator.preparation.task_input import create_human_selected_task
from stock_research_llm_orchestrator.research.input import build_input, serialize, validate_input
from stock_research_llm_orchestrator.research.workflow import (
    ResearchRequest,
    run_workflow,
    validate_bundle,
)


ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "tests/fixtures/research"
INSTRUCTIONS = ROOT / "agent-sources/detailed-analysis/v1"
STAMP = "2026-10-30T00:00:00+00:00"


def _source() -> tuple[bytes, dict[str, bytes]]:
    task = create_human_selected_task(
        "1234",
        1,
        mic="XTKS",
        task_id_factory=lambda: "research-fixture-task",
        accepted_at_factory=lambda: datetime(2026, 10, 29, tzinfo=UTC),
    )
    data = DraftInput.model_validate_json(
        json.dumps(
            {
                "security_code": "1234",
                "exchange": "XTKS",
                "external_llm_transfer_allowed": True,
                "evidence": [
                    {
                        "evidence_id": "ir-excerpt",
                        "source_id": "issuer-ir",
                        "approval_version": 1,
                        "source_url": "https://example.com/ir/report.pdf",
                        "retrieved_at": "2026-10-29T00:00:00Z",
                        "source_sha256": "a" * 64,
                        "text": "Synthetic test excerpt. Business durability and current price remain unverified.",
                    }
                ],
            }
        )
    )
    draft = DraftOutput.model_validate_json(
        json.dumps({"claims": [], "missing_information": ["Fixture only"], "follow_up_requests": []})
    )
    return serialize(task), build_bundle(data, draft)


def _inputs() -> dict[str, bytes]:
    task, prior = _source()
    return build_input(
        task,
        prior,
        (ROOT / "config/policies/detailed-analysis/v1.yaml").read_bytes(),
        {"issuer-ir-v1": (ROOT / "docs/decisions/0007-use-llm-led-evidence-follow-up.md").read_bytes()},
        STAMP,
    )


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


def test_independent_pass_replays_and_never_claims_final_completion() -> None:
    """Both workers receive the same neutral evidence before review and report generation."""
    requests = []

    def invoke(request: ResearchRequest) -> bytes:
        requests.append(request)
        return _invoke(request)

    files = run_workflow(_inputs(), invoke, INSTRUCTIONS, mode="replay")
    validate_bundle(files)
    assert [request.stage for request in requests] == [
        "codex_worker",
        "claude_worker",
        "orchestrator",
        "primary_reviewer",
        "translation",
    ]
    assert requests[0].data == requests[1].data
    assert "workers" not in requests[0].data
    assert "Fixture only" not in json.dumps(requests[0].data)
    assert requests[2].provider == "codex"
    assert requests[3].provider == "claude"
    manifest = json.loads(files["manifest.json"])
    assert manifest["analysis_completed"] is False
    assert manifest["analysis_ready"] is False
    assert manifest["mode"] == "replay"
    report = files["report.md"].decode()
    assert "一次レビュー" in report and "英語原文" in report
    assert "difference-long-term-durability" in report
    assert "not_evaluable" in report
    assert not any("final-analysis-result" in name for name in files)


@pytest.mark.parametrize(
    "changed",
    [
        "input/common-input.json",
        "codex_worker.json",
        "responses/codex_worker.json",
        "report.md",
        "requests/claude_worker.json",
    ],
)
def test_offline_validation_rejects_changed_bytes(changed: str) -> None:
    """Changing inputs, adopted output, raw responses or rendered text breaks replay."""
    files = run_workflow(_inputs(), _invoke, INSTRUCTIONS, mode="replay")
    files[changed] += b" "
    with pytest.raises(ValueError):
        validate_bundle(files)


def test_input_preserves_upstream_and_excerpt_hashes_separately() -> None:
    """An original document hash never becomes the hash of its selected excerpt."""
    files = _inputs()
    validate_input(files)
    excerpt = json.loads(files["common-input.json"])[0]
    assert excerpt["upstream_source_sha256"] == "a" * 64
    assert excerpt["excerpt_sha256"] != "a" * 64
    task, prior = _source()
    changed = json.loads(task)
    changed["security"]["security_code"] = "5678"
    with pytest.raises(ValueError, match="research_task_input_mismatch"):
        build_input(
            json.dumps(changed).encode(),
            prior,
            files["policy.yaml"],
            {"issuer-ir-v1": files["approvals/issuer-ir-v1"]},
            STAMP,
        )


def test_high_review_finding_is_retained_and_blocks_review_pass() -> None:
    """The first reviewer cannot have a major finding silently closed by orchestration."""

    def invoke(request: ResearchRequest) -> bytes:
        if request.stage != "primary_reviewer":
            return _invoke(request)
        payload = json.loads(_invoke(request))
        payload.update(overall_status="rework", review_passed=False)
        payload["completion_gate"]["finding_processing_complete"] = False
        payload["findings"] = [
            {
                "finding_id": "finding-missing-evidence",
                "target_artifact_reference": request.data["synthesis_reference"],
                "target_claim_ids": ["codex-lt-business"],
                "json_pointer": "/horizon_results/1",
                "horizon": "long_term",
                "summary": "Material durability evidence is missing.",
                "detail": "The evidence does not establish durability.",
                "primary_classification": "insufficient_evidence",
                "secondary_classifications": [],
                "classification_reason": "No verified business history is available.",
                "classification_pending": None,
                "severity": "high",
                "severity_reason": "Evaluability could change.",
                "evidence_ids": ["ir-excerpt"],
                "policy_references": [request.data["policy_reference"]],
                "schema_references": [],
                "materiality": {"impacts": [], "is_material": False},
                "expected_resolution": "Obtain and verify business history.",
                "lifecycle": "open",
                "unresolved_retention_reason": None,
            }
        ]
        return json.dumps(payload).encode()

    files = run_workflow(_inputs(), invoke, INSTRUCTIONS, mode="replay")
    manifest = json.loads(files["manifest.json"])
    assert manifest["review_passed"] is False
    assert manifest["unresolved_finding_ids"] == ["finding-missing-evidence"]
    assert "finding-missing-evidence" in files["report.md"].decode()


def test_invented_worker_citation_fails_before_peer_execution() -> None:
    """Unvalidated output is rejected before it reaches subsequent roles."""
    observed = []

    def invoke(request: ResearchRequest) -> bytes:
        observed.append(request.stage)
        result = json.loads(_invoke(request))
        result["horizon_analyses"][0]["claims"][0]["evidence_ids"] = ["invented"]
        return json.dumps(result).encode()

    with pytest.raises(ValueError, match="research_reference_invalid"):
        run_workflow(_inputs(), invoke, INSTRUCTIONS, mode="replay")
    assert observed == ["codex_worker"]


def test_public_replay_publishes_and_validates_without_live_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The public offline entrypoint uses exact saved responses and keeps existing output immutable."""
    files = run_workflow(_inputs(), _invoke, INSTRUCTIONS, mode="replay")
    task, prior = _source()
    task_path = tmp_path / "task.json"
    task_path.write_bytes(task)
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
        "--workflow-version",
        "1",
        "--task",
        str(task_path),
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
    assert cli.main(args) == 3
    assert (tmp_path / "result/report.md").read_bytes() == files["report.md"]


def test_provider_failure_does_not_publish_a_completed_bundle(tmp_path: Path) -> None:
    """A failed role never creates a successful public result directory."""

    def fail(_: ResearchRequest) -> bytes:
        raise RuntimeError("synthetic provider failure")

    with pytest.raises(RuntimeError):
        files = run_workflow(_inputs(), fail, INSTRUCTIONS, mode="replay")
        publish_preparation(tmp_path, "result", files, validator=validate_bundle)
    assert not (tmp_path / "result").exists()


def test_price_selection_keeps_last_observation_and_missing_close_without_bulk_transfer() -> None:
    """A pending final close is retained as missing rather than replaced by a previous close."""
    from stock_research_llm_orchestrator.research.input import excerpt_text

    row = {
        "on": "2026-10-28",
        "open": "100",
        "high": "102",
        "low": "99",
        "close": "101",
        "adjusted_close": "101",
        "volume": "10",
        "dividends": "0",
        "stock_splits": "0",
    }
    payload = {
        "quality_passed": False,
        "issues": [{"code": "missing_close"}],
        "limitations": ["Final close pending"],
        "normalized_prices": {
            "currency": "JPY",
            "source_timezone": "Asia/Tokyo",
            "requested_period": {"start_date": "2026-10-28", "end_date": "2026-10-29"},
            "observed_period": {"start_date": "2026-10-28", "end_date": "2026-10-29"},
            "three_year_request": False,
            "trading_day_coverage": "unconfirmed",
            "issues": [],
            "rows": [row, {**row, "on": "2026-10-29", "close": None, "adjusted_close": None}],
        },
    }
    selected = json.loads(excerpt_text("yfinance", json.dumps(payload)))
    assert selected["last_observed_row"]["on"] == "2026-10-29"
    assert selected["last_observed_row"]["close"] is None
    assert selected["quality_passed"] is False
    assert "rows" not in selected
    assert "2026-10-28" not in json.dumps(selected)


def test_input_creates_traceable_child_task_instead_of_resetting_parent_evidence_version() -> None:
    """Independent excerpt snapshots belong to a new task with the original task retained."""
    files = _inputs()
    parent = json.loads(files["parent-task.json"])
    task = json.loads(files["task.json"])
    evidence = json.loads(files["evidence-set.json"])
    assert task["task_id"] != parent["task_id"]
    assert task["parent_task_id"] == parent["task_id"]
    assert evidence["task_id"] == task["task_id"]
    assert evidence["evidence_set_version"] == 1


@pytest.mark.parametrize("stage", ["orchestrator", "primary_reviewer", "translation"])
def test_invalid_downstream_output_cannot_be_adopted(stage: str) -> None:
    """Invalid citations, review targets, and incomplete translation each stop the pipeline."""
    observed = []

    def invoke(request: ResearchRequest) -> bytes:
        observed.append(request.stage)
        payload = json.loads(_invoke(request))
        if request.stage == stage:
            if stage == "orchestrator":
                payload["differences"][0]["claude_claim_ids"] = ["codex-lt-business"]
            elif stage == "primary_reviewer":
                payload["review_passed"] = False
            else:
                payload["translations"].pop()
        return json.dumps(payload).encode()

    with pytest.raises(ValueError):
        run_workflow(_inputs(), invoke, INSTRUCTIONS, mode="replay")
    assert observed[-1] == stage


def test_live_path_keeps_evidence_times_distinct_from_response_creation_time() -> None:
    """A delayed provider response cannot silently replace the common evidence timestamps."""
    files = run_workflow(_inputs(), _invoke, INSTRUCTIONS, mode="live")
    evidence = json.loads(files["input/evidence-set.json"])
    for role in ("codex_worker", "claude_worker"):
        worker = json.loads(files[f"{role}.json"])
        assert worker["evidence_frozen_at"] == evidence["evidence_frozen_at"]
        assert worker["freshness_checked_at"] == evidence["freshness_checked_at"]
        assert datetime.fromisoformat(worker["created_at"]) > datetime.fromisoformat(evidence["evidence_frozen_at"])
    validate_bundle(files)
