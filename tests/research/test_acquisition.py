"""Exercise standard acquisition turns, evidence epochs and exact offline replay."""

import json
from hashlib import sha256
from typing import cast

import pytest
from pydantic import JsonValue

from stock_research_llm_orchestrator.agents.configuration import load_agent_configuration
from stock_research_llm_orchestrator.agents.research_provider import WebTurnResult
from stock_research_llm_orchestrator.contracts.base import FileReferenceV1
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.acquisition_analysis import (
    AcquiredEvidence,
    AcquisitionRequest,
    AcquisitionResult,
)
from stock_research_llm_orchestrator.preparation.research_acquisition import request_sha256
from stock_research_llm_orchestrator.research.acquisition import replay_saved_bundle, run_workflow, validate_bundle
from stock_research_llm_orchestrator.research.directed import DirectedMainTurn, ReviewResponse
from stock_research_llm_orchestrator.research.workflow import ResearchRequest

from .test_configured import PROFILE, ROOT
from .test_web import _invoke as web_response
from .test_workflow import _inputs


INSTRUCTIONS = ROOT / "agent-sources/detailed-analysis/acquisition-v1"
RESEARCH_ROLES = (
    "track_a_capital",
    "track_a_income",
    "track_b_capital",
    "track_b_income",
    "track_a_aggregator",
    "track_b_aggregator",
    "orchestrator",
    "primary_reviewer",
)


def _request(purpose: str = "Read an original filing") -> AcquisitionRequest:
    return AcquisitionRequest(
        operation="public_document",
        security_code="1234",
        purpose=purpose,
        url="https://example.com/filing",
        classification="public-web",
    )


def _reference(path: str, body: bytes) -> FileReferenceV1:
    return FileReferenceV1(
        artifact_id="test-file", relative_path=path, media_type="text/plain", sha256=sha256(body).hexdigest()
    )


def _acquire(request: AcquisitionRequest, role: str, turn: int) -> tuple[AcquisitionResult, dict[str, bytes]]:
    del role, turn
    raw = b"A neutral original filing."
    metadata = json.dumps(
        {
            "media_type": "text/plain",
            "url": request.url,
            "classification": request.classification,
            "retrieved_at": "2026-11-14T00:00:00+00:00",
        }
    ).encode()
    attempt = b'{"event":"physical_attempt"}'
    files = {"filing.txt": raw, "document-metadata.json": metadata, "attempt.json": attempt}
    refs = tuple(_reference(path, body) for path, body in files.items())
    evidence = AcquiredEvidence(
        evidence_id="acquired-filing",
        source_id="public-web",
        source_reference=request.url or "https://example.com/filing",
        retrieved_at="2026-11-14T00:00:00+00:00",
        raw_reference=refs[0],
        text=raw.decode(),
        validation="verified",
    )
    return AcquisitionResult(
        request_sha256=request_sha256(request),
        status="acquired",
        validation="verified",
        requested_at="2026-11-14T00:00:00+00:00",
        completed_at="2026-11-14T00:00:00+00:00",
        attempt_references=(refs[2],),
        files=refs,
        evidence=(evidence,),
        detail="Actual synthetic original acquired.",
    ), files


def _final(request: ResearchRequest) -> WebTurnResult:
    if request.response_model is ReviewResponse:
        return WebTurnResult(b'{"decision":"accept","reason":"The synthesis stands.","rerun_request":null}', b"")
    turn = web_response(request)
    if request.stage == "translation":
        return turn
    body: dict[str, object] = {"kind": "final", "requests": [], "state": "done", "payload": json.loads(turn.body)}
    if request.response_model is DirectedMainTurn:
        body["rerun_request"] = None
    return WebTurnResult(json.dumps(body).encode(), turn.trace)


def _acquisition_turn(purpose: str = "Read an original filing") -> WebTurnResult:
    return WebTurnResult(
        json.dumps(
            {
                "kind": "acquire",
                "requests": [_request(purpose).model_dump(mode="json")],
                "state": "Need original context",
                "payload": None,
            }
        ).encode(),
        b"",
    )


@pytest.mark.parametrize("selected_role", RESEARCH_ROLES)
def test_every_research_role_can_acquire_and_resume_without_translation_acquisition(selected_role: str) -> None:
    """All eight roles receive actual results; native Web is counted across their turns."""
    observed: list[ResearchRequest] = []
    acquired: list[str] = []
    asked: set[str] = set()

    def invoke(request: ResearchRequest) -> WebTurnResult:
        observed.append(request)
        if request.stage == selected_role and request.stage not in asked:
            asked.add(request.stage)
            return _acquisition_turn(request.stage)
        if request.data.get("own_previous_turns"):
            assert request.data["last_acquisition_results"]
            assert request.data["continuation_state"] == "Need original context"
        return _final(request)

    def acquire(request: AcquisitionRequest, role: str, turn: int) -> tuple[AcquisitionResult, dict[str, bytes]]:
        acquired.append(role)
        return _acquire(request, role, turn)

    files = run_workflow(
        _inputs(), load_agent_configuration(PROFILE), invoke, INSTRUCTIONS, mode="replay", acquire=acquire
    )
    assert acquired == [selected_role]
    assert len([r for r in observed if r.stage == "translation"]) == 1
    assert json.loads(files["manifest.json"])["epochs"] == 2
    validate_bundle(files)
    replayed = replay_saved_bundle(files, _inputs(), load_agent_configuration(PROFILE))
    assert replayed == files


def test_initial_specialists_keep_private_results_until_common_distribution() -> None:
    """No peer reasoning or newly acquired IDs reach another initial specialist."""
    initial = []
    asked = False

    def invoke(request: ResearchRequest) -> WebTurnResult:
        nonlocal asked
        if (
            request.stage in RESEARCH_ROLES[:4]
            and request.data["evidence_epoch"] == 1
            and not request.data["own_previous_turns"]
        ):
            initial.append(request)
            assert request.data["acquired_evidence"] == []
            assert "specialists" not in request.data
            assert "previous_web_sources" not in request.data
        if request.stage == "track_a_capital" and not asked:
            asked = True
            return _acquisition_turn()
        return _final(request)

    files = run_workflow(
        _inputs(), load_agent_configuration(PROFILE), invoke, INSTRUCTIONS, mode="replay", acquire=_acquire
    )
    assert len(initial) == 4
    updates = json.loads(files["evidence-distributions.json"])
    assert updates[0]["recipients"] == list(RESEARCH_ROLES[:4])
    for role in RESEARCH_ROLES[:4]:
        artifact = json.loads(files[f"{role}.json"])
        snapshot_id = artifact["evidence_snapshot_reference"]["artifact_id"]
        saved_snapshot = json.loads(files[f"snapshots/{snapshot_id}.json"])
        assert saved_snapshot["evidence"][0]["evidence_id"] == "acquired-filing"
        raw_ref = saved_snapshot["evidence"][0]["raw_reference"]
        assert sha256(files[raw_ref["relative_path"]]).hexdigest() == raw_ref["sha256"]


def test_late_review_acquisition_invalidates_all_upstream_results() -> None:
    """A reviewer cannot publish conclusions based on a superseded evidence epoch."""
    asked = False

    def invoke(request: ResearchRequest) -> WebTurnResult:
        nonlocal asked
        if request.stage == "primary_reviewer" and not asked:
            asked = True
            return _acquisition_turn()
        return _final(request)

    files = run_workflow(
        _inputs(), load_agent_configuration(PROFILE), invoke, INSTRUCTIONS, mode="replay", acquire=_acquire
    )
    assert json.loads(files["manifest.json"])["epochs"] == 2
    assert "epochs/1/orchestrator.json" in files and "epochs/2/orchestrator.json" in files
    assert files["orchestrator.json"] == files["epochs/2/orchestrator.json"]


def test_unchanged_requests_stall_without_publishing_report_or_repeating_network() -> None:
    """A repeated identical request returns a notice before recording a stalled run."""
    acquisitions = 0
    notices: list[object] = []

    def invoke(request: ResearchRequest) -> WebTurnResult:
        notices.append(request.data.get("no_progress_notice"))
        return _acquisition_turn()

    def acquire(request: AcquisitionRequest, role: str, turn: int) -> tuple[AcquisitionResult, dict[str, bytes]]:
        nonlocal acquisitions
        acquisitions += 1
        return _acquire(request, role, turn)

    files = run_workflow(
        _inputs(), load_agent_configuration(PROFILE), invoke, INSTRUCTIONS, mode="replay", acquire=acquire
    )
    assert acquisitions == 1
    assert notices[-1]
    assert json.loads(files["manifest.json"])["status"] == "stalled"
    assert "report.md" not in files
    validate_bundle(files)


@pytest.mark.parametrize("target", ["request", "raw", "snapshot", "response", "trace"])
def test_tampered_bindings_are_rejected_without_network(target: str) -> None:
    """The replay binds exact request, raw original, snapshot, final body and native trace."""
    asked = False

    def invoke(request: ResearchRequest) -> WebTurnResult:
        nonlocal asked
        if request.stage == "track_a_capital" and not asked:
            asked = True
            return _acquisition_turn()
        return _final(request)

    files = run_workflow(
        _inputs(), load_agent_configuration(PROFILE), invoke, INSTRUCTIONS, mode="replay", acquire=_acquire
    )
    targets = {
        "request": "acquisitions/0/request.json",
        "raw": "acquisitions/0/files/filing.txt",
        "snapshot": "snapshots/track_a_capital-0.json",
        "response": "responses/track_a_capital-1.json",
        "trace": "provider-traces/track_a_capital-1.jsonl",
    }
    files[targets[target]] += b" "
    with pytest.raises((ValueError, AssertionError)):
        validate_bundle(files)


@pytest.mark.parametrize("status", ["failed", "waiting", "not_attempted"])
def test_unavailable_route_returns_status_then_allows_an_alternative(status: str) -> None:
    """Unavailable outcomes are returned honestly and do not prevent a different public source."""
    turns = 0
    outcomes = []

    def invoke(request: ResearchRequest) -> WebTurnResult:
        nonlocal turns
        if request.stage == "track_a_capital":
            turns += 1
            if turns == 1:
                return _acquisition_turn()
            if turns == 2:
                entries = cast(list[dict[str, JsonValue]], request.data["last_acquisition_results"])
                result = cast(dict[str, JsonValue], entries[0]["result"])
                assert result["status"] == status
                alternative = _request().model_copy(update={"url": "https://example.com/alternative"})
                return WebTurnResult(
                    json.dumps(
                        {
                            "kind": "acquire",
                            "requests": [alternative.model_dump(mode="json")],
                            "state": "Try another original route",
                            "payload": None,
                        }
                    ).encode(),
                    b"",
                )
        return _final(request)

    def acquire(request: AcquisitionRequest, role: str, turn: int) -> tuple[AcquisitionResult, dict[str, bytes]]:
        outcomes.append(request.url)
        if request.url == "https://example.com/alternative":
            return _acquire(request, role, turn)
        raw = b'{"event":"attempted_failure"}'
        files = {"attempt.json": raw} if status == "failed" else {}
        result = AcquisitionResult.model_validate_json(
            json.dumps(
                {
                    "request_sha256": request_sha256(request),
                    "status": status,
                    "validation": "pending",
                    "requested_at": "2026-11-14T00:00:00+00:00",
                    "completed_at": "2026-11-14T00:00:00+00:00",
                    "attempt_references": [_reference("attempt.json", raw).model_dump(mode="json")] if files else [],
                    "detail": "Explicit acquisition status, not an acquired original.",
                }
            )
        )
        return result, files

    files = run_workflow(
        _inputs(), load_agent_configuration(PROFILE), invoke, INSTRUCTIONS, mode="replay", acquire=acquire
    )
    assert outcomes == ["https://example.com/filing", "https://example.com/alternative"]
    assert json.loads(files["manifest.json"])["status"] == "completed"


def test_specialist_native_web_events_can_occur_before_acquisition_resume() -> None:
    """A final turn need not search again when an earlier turn contains a real native tool event."""
    turns = 0

    def invoke(request: ResearchRequest) -> WebTurnResult:
        nonlocal turns
        if request.stage == "track_a_capital":
            turns += 1
            if turns == 1:
                turn = _acquisition_turn()
                return WebTurnResult(turn.body, b'{"type":"item.started","item":{"type":"web_search"}}\n')
            if turns == 2:
                return WebTurnResult(_final(request).body, b"")
        return _final(request)

    files = run_workflow(
        _inputs(), load_agent_configuration(PROFILE), invoke, INSTRUCTIONS, mode="replay", acquire=_acquire
    )
    assert json.loads(files["manifest.json"])["executions"]["track_a_capital"]["native_web_tool_events"] >= 1


def test_evidence_storage_alias_does_not_create_false_new_epoch() -> None:
    """The same immutable source under a second result directory remains the same neutral evidence."""
    from stock_research_llm_orchestrator.research.acquisition import _merge_evidence

    result, _ = _acquire(_request(), "track_a_capital", 0)
    original = result.evidence[0]
    alternate = original.model_copy(
        update={
            "raw_reference": original.raw_reference.model_copy(update={"relative_path": "another-directory/filing.txt"})
        }
    )
    visible = {original.evidence_id: original}
    _merge_evidence(visible, alternate)
    assert visible[original.evidence_id] == original
    promoted = original.model_copy(update={"validation": "pending"})
    _merge_evidence(visible, promoted)
    assert visible[original.evidence_id].validation == "pending"
    with pytest.raises(ValueError, match="identity_collision"):
        _merge_evidence(visible, original.model_copy(update={"text": "Different source content"}))


def test_market_excerpt_retains_dividend_events_without_zero_event_bulk_rows() -> None:
    """Decimal zero variants must not expand the compact dividend/split event summary."""
    from stock_research_llm_orchestrator.research.acquisition import _evidence_excerpt

    result, _ = _acquire(_request(), "track_a_capital", 0)
    evidence = result.evidence[0].model_copy(
        update={
            "source_id": "yfinance",
            "text": json.dumps(
                {
                    "normalized_prices": {
                        "rows": [
                            {"on": "2026-10-28", "dividends": "0.0", "stock_splits": "0.00"},
                            {"on": "2026-10-29", "dividends": "50.0", "stock_splits": "0.0"},
                        ]
                    }
                }
            ),
        }
    )
    excerpt = json.loads(str(_evidence_excerpt(evidence)["text"]))["normalized_prices"]
    assert "rows" not in excerpt
    assert excerpt["row_count"] == 2
    assert excerpt["dividend_observations"] == [{"on": "2026-10-29", "dividends": "50.0"}]
    assert excerpt["split_observations"] == []


def test_result_status_summary_retains_provenance_without_duplicating_original_text() -> None:
    """Acquisition outcome summaries reference originals instead of embedding a second text copy."""
    from stock_research_llm_orchestrator.research.acquisition import _evidence_excerpt, _result_excerpt

    result, _ = _acquire(_request(), "track_a_capital", 0)
    summary = _result_excerpt(result)
    references = cast(list[dict[str, JsonValue]], summary["evidence"])
    assert "text" not in references[0]
    assert references[0]["evidence_id"] == result.evidence[0].evidence_id
    assert references[0]["raw_reference"] == result.evidence[0].raw_reference.model_dump(mode="json")
    assert references[0]["source_reference"] == result.evidence[0].source_reference
    assert summary["status"] == result.status and summary["validation"] == result.validation
    assert summary["attempt_references"] == [item.model_dump(mode="json") for item in result.attempt_references]
    body = json.dumps({"acquisition_results": [summary], "acquired_evidence": [_evidence_excerpt(result.evidence[0])]})
    assert body.count(result.evidence[0].text) == 0
    catalog = json.loads(str(_evidence_excerpt(result.evidence[0])["text"]))
    assert catalog["text_sha256"] == sha256(result.evidence[0].text.encode()).hexdigest()


@pytest.mark.parametrize("correct_after_feedback", [True, False])
def test_wrong_basis_requires_a_new_provider_turn_or_preserves_stalled_run(
    monkeypatch: pytest.MonkeyPatch, correct_after_feedback: bool
) -> None:
    """A paraphrased share-basis identifier is rejected without mutating the provider's answer."""
    monkeypatch.setattr(
        "stock_research_llm_orchestrator.research.acquisition.observed_price",
        lambda _: {
            "price": "100",
            "currency": "JPY",
            "on": "2026-10-29",
            "per_share_basis": "current listed-share basis",
        },
    )
    specialist_calls = 0

    def set_basis(value: object, basis: str) -> None:
        if isinstance(value, dict):
            for name, child in value.items():
                if name == "per_share_basis":
                    value[name] = basis
                else:
                    set_basis(child, basis)
        elif isinstance(value, list):
            for child in value:
                set_basis(child, basis)

    def invoke(request: ResearchRequest) -> WebTurnResult:
        nonlocal specialist_calls
        turn = _final(request)
        if request.stage == "translation":
            return turn
        value = json.loads(turn.body)
        basis = "current listed-share basis"
        if request.stage == "track_a_capital":
            specialist_calls += 1
            if specialist_calls == 1 or not correct_after_feedback:
                basis = "The current listed share basis, with unchanged economic meaning."
            if specialist_calls > 1:
                assert request.data["validation_feedback"]
        set_basis(value, basis)
        return WebTurnResult(json.dumps(value).encode(), turn.trace)

    def no_acquisition(*_: object) -> tuple[AcquisitionResult, dict[str, bytes]]:
        pytest.fail("Basis corrections must not call the acquisition executor")

    files = run_workflow(
        _inputs(), load_agent_configuration(PROFILE), invoke, INSTRUCTIONS, mode="replay", acquire=no_acquisition
    )
    assert specialist_calls == 2
    rejection = json.loads(files["validation-rejections/track_a_capital-0.json"])
    assert rejection["issues"][0]["expected"] == "current listed-share basis"
    first = json.loads(files["responses/track_a_capital-0.json"])
    assert first["payload"]["horizons"][0]["scenarios"][0]["per_share_basis"].startswith("The current")
    manifest = json.loads(files["manifest.json"])
    if correct_after_feedback:
        assert manifest["status"] == "completed" and "report.md" in files
        assert manifest["executions"]["track_a_capital"]["native_web_tool_events"] == 2
    else:
        assert manifest["status"] == "stalled" and "report.md" not in files
        assert "validation-rejections/track_a_capital-1.json" in files
    validate_bundle(files)


@pytest.mark.parametrize("selected_role", RESEARCH_ROLES)
def test_retained_evidence_reads_resume_every_role_without_external_acquisition(
    selected_role: str,
) -> None:
    """Each research role can request exact retained lines after seeing a catalog."""
    steps = 0
    acquisitions = 0

    def invoke(request: ResearchRequest) -> WebTurnResult:
        nonlocal steps
        if request.stage == selected_role:
            steps += 1
            if steps == 1:
                return _acquisition_turn()
            if steps == 2:
                excerpts = cast(list[dict[str, JsonValue]], request.data["acquired_evidence"])
                catalog = json.loads(str(excerpts[0]["text"]))
                assert catalog["line_count"] == 1
                assert "A neutral original filing." not in json.dumps(request.data)
                return WebTurnResult(
                    json.dumps(
                        {
                            "kind": "acquire",
                            "requests": [
                                {
                                    "operation": "read_evidence",
                                    "evidence_id": "acquired-filing",
                                    "query": None,
                                    "start_line": 1,
                                    "end_line": 1,
                                }
                            ],
                            "state": "Read original lines before drawing conclusions",
                            "payload": None,
                        }
                    ).encode(),
                    b"",
                )
            if steps == 3:
                reads = cast(list[dict[str, JsonValue]], request.data["evidence_reads"])
                lines = cast(list[dict[str, JsonValue]], reads[0]["selected_lines"])
                assert lines == [{"line": 1, "text": "A neutral original filing."}]
                assert json.dumps(request.data).count("A neutral original filing.") == 1
        return _final(request)

    def acquire(request: AcquisitionRequest, role: str, turn: int) -> tuple[AcquisitionResult, dict[str, bytes]]:
        nonlocal acquisitions
        acquisitions += 1
        return _acquire(request, role, turn)

    files = run_workflow(
        _inputs(), load_agent_configuration(PROFILE), invoke, INSTRUCTIONS, mode="replay", acquire=acquire
    )
    assert acquisitions == 1
    assert f"evidence-reads/{selected_role}-1-0.json" in files
    assert len(json.loads(files["acquisition-log.json"])) == 1
    assert json.loads(files["manifest.json"])["epochs"] == 2
    validate_bundle(files)
    corrupted = dict(files)
    corrupted[f"evidence-reads/{selected_role}-1-0.json"] += b" "
    with pytest.raises(ValueError):
        validate_bundle(corrupted)


def test_repeated_unavailable_evidence_read_stalls_without_external_calls() -> None:
    """Unknown evidence stays unavailable and repeated identical reads cannot publish a report."""
    notices = []

    def invoke(request: ResearchRequest) -> WebTurnResult:
        notices.append(request.data.get("no_progress_notice"))
        return WebTurnResult(
            json.dumps(
                {
                    "kind": "acquire",
                    "requests": [
                        {
                            "operation": "read_evidence",
                            "evidence_id": "unseen-evidence",
                            "query": None,
                            "start_line": 1,
                            "end_line": 1,
                        }
                    ],
                    "state": "Attempt an unavailable source",
                    "payload": None,
                }
            ).encode(),
            b"",
        )

    def no_acquisition(*_: object) -> tuple[AcquisitionResult, dict[str, bytes]]:
        pytest.fail("Retained evidence reads must not acquire from the network")

    files = run_workflow(
        _inputs(), load_agent_configuration(PROFILE), invoke, INSTRUCTIONS, mode="replay", acquire=no_acquisition
    )
    assert len(notices) == 3 and notices[-1]
    assert json.loads(files["acquisition-log.json"]) == []
    assert "report.md" not in files
    assert json.loads(files["manifest.json"])["status"] == "stalled"
    validate_bundle(files)


def test_private_pending_evidence_follows_only_actual_dependency_lineage() -> None:
    """Native pending observations follow actual dependencies without cross-track promotion."""
    from datetime import date

    from .test_baseline import _acquire as acquire_native_fixture

    asked_epochs: set[int] = set()
    seen: dict[str, set[str]] = {}
    market_request = AcquisitionRequest(
        operation="market",
        security_code="1234",
        purpose="Inspect a missing native observation",
        start=date(2026, 10, 28),
        end=date(2026, 10, 30),
    )

    def set_basis(value: object, basis: str) -> None:
        if isinstance(value, dict):
            for name, child in value.items():
                if name == "per_share_basis":
                    value[name] = basis
                else:
                    set_basis(child, basis)
        elif isinstance(value, list):
            for child in value:
                set_basis(child, basis)

    def invoke(request: ResearchRequest) -> WebTurnResult:
        if request.stage == "translation":
            return _final(request)
        if not request.data.get("own_previous_turns"):
            excerpts = cast(list[dict[str, JsonValue]], request.data["acquired_evidence"])
            seen[request.stage] = {str(item["evidence_id"]) for item in excerpts}
        epoch = cast(int, request.data["evidence_epoch"])
        if request.stage == "track_a_capital" and epoch not in asked_epochs:
            asked_epochs.add(epoch)
            return WebTurnResult(
                json.dumps(
                    {
                        "kind": "acquire",
                        "requests": [market_request.model_dump(mode="json")],
                        "state": "Inspect the actual native market result",
                        "payload": None,
                    }
                ).encode(),
                b"",
            )
        turn = _final(request)
        body = json.loads(turn.body)
        if request.stage == "track_a_capital":
            for horizon in body["payload"]["horizons"]:
                for claim in horizon["claims"]:
                    claim["evidence_ids"] = ["baseline-market"]
        price = cast(dict[str, JsonValue] | None, request.data.get("observed_price"))
        if price is not None:
            set_basis(body, str(price["per_share_basis"]))
        return WebTurnResult(json.dumps(body).encode(), turn.trace)

    def acquire(request: AcquisitionRequest, role: str, turn: int) -> tuple[AcquisitionResult, dict[str, bytes]]:
        assert role == "track_a_capital"
        result, files = acquire_native_fixture(request, "baseline", turn)
        return result, dict(files)

    files = run_workflow(
        _inputs(), load_agent_configuration(PROFILE), invoke, INSTRUCTIONS, mode="replay", acquire=acquire
    )
    for role in RESEARCH_ROLES[:4]:
        assert "baseline-market" not in seen[role]
    assert "baseline-market" in seen["track_a_aggregator"]
    assert "baseline-market" not in seen["track_b_aggregator"]
    assert "baseline-market" in seen["orchestrator"]
    assert "baseline-market" in seen["primary_reviewer"]
    # The listing verification becomes common; the pending market observation does not.
    assert json.loads(files["manifest.json"])["epochs"] == 2
    distributions = json.loads(files["evidence-distributions.json"])
    assert all("baseline-market" not in update["evidence_ids"] for update in distributions)
    for role in ("track_a_aggregator", "orchestrator", "primary_reviewer"):
        artifact = json.loads(files[f"{role}.json"])
        snapshot_id = artifact["evidence_snapshot_reference"]["artifact_id"]
        evidence = json.loads(files[f"snapshots/{snapshot_id}.json"])["evidence"]
        native = next(item for item in evidence if item["evidence_id"] == "baseline-market")
        assert native["validation"] == "pending"
        raw_ref = native["raw_reference"]
        assert sha256(files[raw_ref["relative_path"]]).hexdigest() == raw_ref["sha256"]
    validate_bundle(files)


def test_completed_turns_resume_only_exact_requests_and_preserve_timestamps_offline() -> None:
    """Retained real-request records bind resumed responses and keep immutable artifact references stable."""

    def no_acquisition(*_: object) -> tuple[AcquisitionResult, dict[str, bytes]]:
        pytest.fail("This completed-turn resume has no external acquisitions")

    config = load_agent_configuration(PROFILE)
    original = run_workflow(_inputs(), config, _final, INSTRUCTIONS, mode="replay", acquire=no_acquisition)
    stamps = json.loads(original["timestamps.json"])

    def retained(request: ResearchRequest) -> WebTurnResult:
        key = request.stage if request.stage == "translation" else request.stage + "-0"
        saved = json.loads(original[f"requests/{key}.json"])
        record = {name: saved[name] for name in ("instructions", "data", "output_schema", "agent")}
        record["stage"] = request.stage
        return WebTurnResult(
            original[f"responses/{key}.json"], original[f"provider-traces/{key}.jsonl"], json.dumps(record).encode()
        )

    resumed = run_workflow(
        _inputs(), config, retained, INSTRUCTIONS, mode="live", acquire=no_acquisition, resume_stamps=stamps
    )
    assert json.loads(resumed["timestamps.json"]) == stamps
    for role in RESEARCH_ROLES:
        assert resumed[f"{role}.json"] == original[f"{role}.json"]
    origins = json.loads(resumed["execution-origins.json"])
    assert set(origins.values()) == {"retained_exact_request"}
    assert set(json.loads(original["execution-origins.json"]).values()) == {"live"}
    validate_bundle(resumed)
    replayed = replay_saved_bundle(resumed, _inputs(), config)
    assert replayed["execution-origins.json"] == resumed["execution-origins.json"]
    assert replayed["track_a_capital.json"] == resumed["track_a_capital.json"]
    tampered = dict(resumed)
    tampered["retained-requests/track_a_capital-0.json"] += b" "
    with pytest.raises(ValueError):
        validate_bundle(tampered)


@pytest.mark.parametrize("changed", ["stage", "instructions", "data", "output_schema", "agent"])
def test_retained_request_must_match_every_actual_provider_input(changed: str) -> None:
    """A completed response cannot be transplanted across different tasks, instructions or agents."""

    def invoke(request: ResearchRequest) -> WebTurnResult:
        record: dict[str, object] = {
            "stage": request.stage,
            "instructions": request.instructions,
            "data": request.data,
            "output_schema": request.response_model.model_json_schema(),
            "agent": request.agent.model_dump(mode="json") if request.agent else None,
        }
        record[changed] = "different"
        turn = _final(request)
        return WebTurnResult(turn.body, turn.trace, json.dumps(record).encode())

    def no_acquisition(*_: object) -> tuple[AcquisitionResult, dict[str, bytes]]:
        pytest.fail("Mismatched retained requests must not invoke external acquisition")

    with pytest.raises(ValueError, match="retained_request_mismatch"):
        run_workflow(
            _inputs(), load_agent_configuration(PROFILE), invoke, INSTRUCTIONS, mode="replay", acquire=no_acquisition
        )


def test_incremental_reassessment_carries_only_own_analysis_and_hash_bound_reads() -> None:
    """New cohorts reuse only their own unchanged-source work and still allow new acquisitions."""
    acquired_targets: list[str] = []
    stages: dict[tuple[int, str], int] = {}
    checked: set[str] = set()

    def external_request(name: str) -> WebTurnResult:
        request = _request().model_copy(update={"url": "https://example.com/" + name})
        return WebTurnResult(
            json.dumps(
                {
                    "kind": "acquire",
                    "requests": [request.model_dump(mode="json")],
                    "state": "Inspect an original source",
                    "payload": None,
                }
            ).encode(),
            b"",
        )

    def invoke(request: ResearchRequest) -> WebTurnResult:
        if request.stage == "translation":
            return _final(request)
        epoch = cast(int, request.data["evidence_epoch"])
        stage = (epoch, request.stage)
        count = stages.get(stage, 0)
        stages[stage] = count + 1
        if epoch == 1:
            assert "own_previous_analysis" not in request.data
            assert "Incremental reassessment:" not in request.instructions
        elif request.stage in RESEARCH_ROLES[:4]:
            previous = cast(dict[str, JsonValue], request.data["own_previous_analysis"])
            assert previous["role"] == request.stage
            assert "Incremental reassessment:" in request.instructions
            if request.stage in {"track_a_capital", "track_b_capital"}:
                reads = cast(list[dict[str, JsonValue]], request.data["evidence_reads"])
                expected_id = "own-a" if request.stage == "track_a_capital" else "own-b"
                assert {read["source_evidence_id"] for read in reads} == {expected_id}
                assert request.data["own_previous_acquisition_results"]
                checked.add(request.stage)
        else:
            # Organizers/main/review are first reached only after epoch 2's new source is distributed.
            assert "own_previous_analysis" not in request.data
            assert "new_common_evidence_ids" not in request.data
            assert "Incremental reassessment:" not in request.instructions
        if epoch == 1 and request.stage in {"track_a_capital", "track_b_capital"}:
            source = "own-a" if request.stage == "track_a_capital" else "own-b"
            if count == 0:
                return external_request(source)
            if count == 1:
                return WebTurnResult(
                    json.dumps(
                        {
                            "kind": "acquire",
                            "requests": [
                                {
                                    "operation": "read_evidence",
                                    "evidence_id": source,
                                    "query": None,
                                    "start_line": 1,
                                    "end_line": 1,
                                }
                            ],
                            "state": "Read exact retained lines",
                            "payload": None,
                        }
                    ).encode(),
                    b"",
                )
        if epoch == 2 and request.stage == "track_a_capital" and count == 0:
            assert set(cast(list[str], request.data["new_common_evidence_ids"])) == {"own-a", "own-b"}
            return external_request("extra-c")
        return _final(request)

    def acquire(request: AcquisitionRequest, role: str, turn: int) -> tuple[AcquisitionResult, dict[str, bytes]]:
        acquired_targets.append(request.url or "")
        result, files = _acquire(request, role, turn)
        source = (request.url or "").rsplit("/", 1)[-1]
        evidence = result.evidence[0].model_copy(update={"evidence_id": source})
        return result.model_copy(update={"evidence": (evidence,)}), files

    files = run_workflow(
        _inputs(),
        load_agent_configuration(PROFILE),
        invoke,
        INSTRUCTIONS,
        mode="replay",
        acquire=acquire,
        reassessment_from_epoch=2,
    )
    assert checked == {"track_a_capital", "track_b_capital"}
    assert len(acquired_targets) == 3
    assert json.loads(files["manifest.json"])["epochs"] == 3
    assert json.loads(files["manifest.json"])["reassessment_from_epoch"] == 2
    validate_bundle(files)
    replayed = replay_saved_bundle(files, _inputs(), load_agent_configuration(PROFILE))
    assert replayed == files


def test_disabled_reassessment_keeps_full_research_requests_and_manifest_unchanged() -> None:
    """The opt-in mode adds no fields or instructions to the existing full-research workflow."""
    seen: list[ResearchRequest] = []

    def invoke(request: ResearchRequest) -> WebTurnResult:
        seen.append(request)
        return _final(request)

    def no_acquisition(*_: object) -> tuple[AcquisitionResult, dict[str, bytes]]:
        pytest.fail("This full-research run needs no acquisition")

    config = load_agent_configuration(PROFILE)
    files = run_workflow(_inputs(), config, invoke, INSTRUCTIONS, mode="replay", acquire=no_acquisition)
    assert "reassessment_from_epoch" not in json.loads(files["manifest.json"])
    assert "batch_common_updates_from_epoch" not in json.loads(files["manifest.json"])
    for request in seen:
        assert "own_previous_analysis" not in request.data
        assert "new_common_evidence_ids" not in request.data
        assert "Incremental reassessment:" not in request.instructions
        assert "another mirror does not certify a trading calendar" not in request.instructions
    assert replay_saved_bundle(files, _inputs(), config) == files


def test_reassessment_read_carry_requires_visible_identity_and_exact_full_text_hash() -> None:
    """A stale hash or another source ID cannot become valid merely through prior role state."""
    from stock_research_llm_orchestrator.research.acquisition import _current_own_reads

    result, _ = _acquire(_request(), "track_a_capital", 0)
    item = result.evidence[0]
    correct: dict[str, JsonValue] = {
        "source_evidence_id": item.evidence_id,
        "source_text_sha256": sha256(item.text.encode()).hexdigest(),
        "status": "read",
    }
    stale: dict[str, JsonValue] = {**correct, "source_text_sha256": "0" * 64}
    unseen: dict[str, JsonValue] = {**correct, "source_evidence_id": "another-role-source"}
    assert _current_own_reads([correct, stale, unseen], {item.evidence_id: item}, {}) == [correct]
    rejected = item.model_copy(update={"validation": "rejected"})
    assert _current_own_reads([correct], {item.evidence_id: rejected}, {}) == []


@pytest.mark.parametrize("stable_from", [None, 2])
def test_receipt_only_listing_refresh_does_not_restart_opted_in_common_epoch(stable_from: int | None) -> None:
    """A newly retained receipt of identical JPX bytes is audited without another full cohort."""
    from datetime import date

    from .test_baseline import _acquire as acquire_native_fixture

    asked: set[str] = set()

    def set_basis(value: object, basis: str) -> None:
        if isinstance(value, dict):
            for name, child in value.items():
                if name == "per_share_basis":
                    value[name] = basis
                else:
                    set_basis(child, basis)
        elif isinstance(value, list):
            for child in value:
                set_basis(child, basis)

    def invoke(request: ResearchRequest) -> WebTurnResult:
        if request.stage in {"track_a_capital", "track_a_aggregator"} and request.stage not in asked:
            asked.add(request.stage)
            acquisition = (
                AcquisitionRequest(operation="listing", security_code="1234", purpose="Initial identity")
                if request.stage == "track_a_capital"
                else AcquisitionRequest(
                    operation="market",
                    security_code="1234",
                    purpose="Refresh standard market reference",
                    start=date(2026, 10, 28),
                    end=date(2026, 10, 30),
                )
            )
            return WebTurnResult(
                json.dumps(
                    {
                        "kind": "acquire",
                        "requests": [acquisition.model_dump(mode="json")],
                        "state": "Acquire actual source evidence",
                        "payload": None,
                    }
                ).encode(),
                b"",
            )
        turn = _final(request)
        if request.stage != "translation" and request.data.get("observed_price"):
            payload = json.loads(turn.body)
            price = cast(dict[str, JsonValue], request.data["observed_price"])
            set_basis(payload, str(price["per_share_basis"]))
            return WebTurnResult(json.dumps(payload).encode(), turn.trace)
        return turn

    def acquire(request: AcquisitionRequest, role: str, turn: int) -> tuple[AcquisitionResult, dict[str, bytes]]:
        assert role in {"track_a_capital", "track_a_aggregator"}
        result, raw = acquire_native_fixture(request, "baseline", turn)
        if request.operation == "market":
            refreshed = tuple(
                item.model_copy(update={"retrieved_at": "2026-11-01T00:00:00+00:00"})
                if item.source_id == "jpx"
                else item
                for item in result.evidence
            )
            result = result.model_copy(
                update={
                    "requested_at": "2026-11-01T00:00:00+00:00",
                    "completed_at": "2026-11-01T00:00:00+00:00",
                    "evidence": refreshed,
                }
            )
        return result, dict(raw)

    config = load_agent_configuration(PROFILE)
    files = run_workflow(
        _inputs(), config, invoke, INSTRUCTIONS, mode="replay", acquire=acquire, stable_evidence_from_epoch=stable_from
    )
    manifest = json.loads(files["manifest.json"])
    assert manifest["epochs"] == (2 if stable_from is not None else 3)
    assert ("stable_evidence_from_epoch" in manifest) == (stable_from is not None)
    # The updated receipt and originals are retained even when no common restart is needed.
    refreshed = json.loads(files["acquisitions/1/result.json"])
    listing = next(item for item in refreshed["evidence"] if item["source_id"] == "jpx")
    assert listing["retrieved_at"] == "2026-11-01T00:00:00+00:00"
    assert files["acquisitions/1/files/" + listing["raw_reference"]["relative_path"]]
    validate_bundle(files)
    assert replay_saved_bundle(files, _inputs(), config) == files


@pytest.mark.parametrize("changed", ["identity", "text", "validation", "source_reference", "raw_sha256"])
def test_stable_common_evidence_guard_keeps_all_substantive_changes(changed: str) -> None:
    """Content, source, validation and original-byte changes still invalidate common evidence."""
    from stock_research_llm_orchestrator.research.acquisition import _common_evidence_changed

    result, _ = _acquire(_request(), "track_a_capital", 0)
    original = result.evidence[0]
    receipt = original.model_copy(
        update={
            "retrieved_at": "2026-11-15T00:00:00+00:00",
            "raw_reference": original.raw_reference.model_copy(update={"relative_path": "another/receipt.txt"}),
        }
    )
    assert not _common_evidence_changed({original.evidence_id: original}, {original.evidence_id: receipt})
    if changed == "identity":
        updated = receipt.model_copy(update={"evidence_id": "new-source-id"})
    elif changed == "raw_sha256":
        updated = receipt.model_copy(
            update={"raw_reference": receipt.raw_reference.model_copy(update={"sha256": "0" * 64})}
        )
    else:
        changes = {
            "text": "Changed original content",
            "validation": "pending",
            "source_reference": "https://example.com/new",
        }
        updated = receipt.model_copy(update={changed: changes[changed]})
    assert _common_evidence_changed({original.evidence_id: original}, {updated.evidence_id: updated})


def test_batched_downstream_sources_reach_all_roles_before_one_fair_reassessment() -> None:
    """Both organizers, synthesis and review finish provisionally before their neutral additions are shared."""
    asked: set[str] = set()
    finals: list[str] = []
    translated = 0
    reassessed: set[str] = set()
    downstream = RESEARCH_ROLES[4:]
    source_ids = {"batch-" + role for role in downstream}

    def invoke(request: ResearchRequest) -> WebTurnResult:
        nonlocal translated
        if request.stage == "translation":
            translated += 1
            assert reassessed == set(RESEARCH_ROLES[:4])
            return _final(request)
        epoch = cast(int, request.data["evidence_epoch"])
        excerpts = cast(list[dict[str, JsonValue]], request.data["acquired_evidence"])
        visible_ids = {str(item["evidence_id"]) for item in excerpts}
        assert "another mirror does not certify a trading calendar" in request.instructions
        if epoch == 1 and request.stage == "track_b_aggregator":
            assert "batch-track_a_aggregator" not in visible_ids
            specialists = cast(dict[str, JsonValue], request.data["specialists"])
            assert set(specialists) == {"track_b_capital", "track_b_income"}
        if epoch == 2 and request.stage in RESEARCH_ROLES[:4]:
            assert translated == 0
            assert visible_ids == source_ids
            assert "specialists" not in request.data
            assert "previous_web_sources" not in request.data
            reassessed.add(request.stage)
        if request.stage in downstream and request.stage not in asked:
            asked.add(request.stage)
            acquisition = _request().model_copy(update={"url": "https://example.com/" + request.stage})
            return WebTurnResult(
                json.dumps(
                    {
                        "kind": "acquire",
                        "requests": [acquisition.model_dump(mode="json")],
                        "state": "Investigate one material downstream source",
                        "payload": None,
                    }
                ).encode(),
                b"",
            )
        finals.append(request.stage)
        return _final(request)

    def acquire(request: AcquisitionRequest, role: str, turn: int) -> tuple[AcquisitionResult, dict[str, bytes]]:
        result, files = _acquire(request, role, turn)
        evidence = result.evidence[0].model_copy(update={"evidence_id": "batch-" + role})
        return result.model_copy(update={"evidence": (evidence,)}), files

    config = load_agent_configuration(PROFILE)
    files = run_workflow(
        _inputs(), config, invoke, INSTRUCTIONS, mode="replay", acquire=acquire, batch_common_updates_from_epoch=1
    )
    assert finals == [*RESEARCH_ROLES, *RESEARCH_ROLES]
    assert translated == 1
    updates = json.loads(files["evidence-distributions.json"])
    assert len(updates) == 1 and set(updates[0]["evidence_ids"]) == source_ids
    assert updates[0]["recipients"] == list(RESEARCH_ROLES[:4])
    assert json.loads(files["manifest.json"])["epochs"] == 2
    assert files["primary_reviewer.json"] == files["epochs/2/primary_reviewer.json"]
    assert "epochs/1/primary_reviewer.json" in files
    validate_bundle(files)
    assert replay_saved_bundle(files, _inputs(), config) == files


def test_batched_mode_keeps_specialist_distribution_boundary_unchanged() -> None:
    """New specialist evidence still reaches all four before any organizer runs."""
    asked = False
    stages: list[tuple[int, str]] = []

    def invoke(request: ResearchRequest) -> WebTurnResult:
        nonlocal asked
        if request.stage == "translation":
            return _final(request)
        epoch = cast(int, request.data["evidence_epoch"])
        stages.append((epoch, request.stage))
        if request.stage == "track_a_capital" and not asked:
            asked = True
            return _acquisition_turn()
        return _final(request)

    files = run_workflow(
        _inputs(),
        load_agent_configuration(PROFILE),
        invoke,
        INSTRUCTIONS,
        mode="replay",
        acquire=_acquire,
        batch_common_updates_from_epoch=1,
    )
    assert {role for epoch, role in stages if epoch == 1} == set(RESEARCH_ROLES[:4])
    assert (2, "track_a_aggregator") in stages
    assert json.loads(files["manifest.json"])["epochs"] == 2
    assert json.loads(files["evidence-distributions.json"])[0]["evidence_ids"] == ["acquired-filing"]
    validate_bundle(files)


@pytest.mark.parametrize("selected", [[], ["track_a_capital"]])
@pytest.mark.parametrize("converted", [True, False])
def test_integration_selects_researchers_and_reuses_pdf_reader(selected: list[str], converted: bool) -> None:
    """Read shared Markdown and replay only the integration-selected specialist work."""
    repeated: list[str] = []
    conversions: list[bytes] = []
    raw = b"%PDF-1.7 synthetic reader input"

    def acquire(request: AcquisitionRequest, role: str, turn: int) -> tuple[AcquisitionResult, dict[str, bytes]]:
        result, files = _acquire(request, role, turn)
        files["filing.txt"] = raw
        reference = _reference("filing.txt", raw)
        return result.model_copy(
            update={
                "files": (reference, *result.files[1:]),
                "evidence": (result.evidence[0].model_copy(update={"raw_reference": reference, "text": raw.decode()}),),
            }
        ), files

    def build_reader(_item: AcquiredEvidence, original: bytes) -> dict[str, bytes]:
        conversions.append(original)
        if not converted:
            return {"status.json": b'{"status":"failed"}'}
        return {"document.md": b"Corrected shared PDF text", "summary.md": b"Shared PDF summary"}

    def invoke(request: ResearchRequest) -> WebTurnResult:
        if request.stage.startswith("reassessment-selection-"):
            catalog = json.dumps(request.data["changed_documents"])
            assert ("Shared PDF summary" if converted else "shared_pdf_reader_unavailable") in catalog
            return WebTurnResult(
                json.dumps({"roles": selected, "reason": "Evaluate only relevant changes"}).encode(), b""
            )
        epoch = request.data.get("evidence_epoch")
        if request.stage == "track_a_capital" and epoch == 1:
            if not request.data.get("last_acquisition_results"):
                return _acquisition_turn()
            if not request.data.get("evidence_reads"):
                return WebTurnResult(
                    b'{"kind":"acquire","requests":[{"operation":"read_evidence","evidence_id":"acquired-filing",'
                    b'"query":null,"start_line":1,"end_line":1}],"state":"Read shared text","payload":null}',
                    b"",
                )
            reads = json.dumps(request.data["evidence_reads"])
            assert ("Corrected shared PDF text" if converted else "not_available") in reads
            assert raw.decode() not in reads
            assert "Never open, download, or read a PDF" in request.instructions
        if request.stage in RESEARCH_ROLES[:4] and epoch == 2:
            repeated.append(request.stage)
        return _final(request)

    config = load_agent_configuration(PROFILE)
    files = run_workflow(
        _inputs(),
        config,
        invoke,
        INSTRUCTIONS,
        mode="replay",
        acquire=acquire,
        execution_profile="parallel_incremental_v5",
        reassessment_from_epoch=2,
        build_pdf_reader=build_reader,
    )
    assert repeated == selected
    assert conversions == [raw]
    if converted:
        assert files[f"pdf-readers/{sha256(raw).hexdigest()}/document.md"] == b"Corrected shared PDF text"
    else:
        assert files[f"pdf-readers/{sha256(raw).hexdigest()}/status.json"] == b'{"status":"failed"}'
        assert not any(name.startswith("responses/document_summary-") for name in files)
    assert replay_saved_bundle(files, _inputs(), config) == files
