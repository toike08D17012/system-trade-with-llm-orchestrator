"""Share document navigation once while preserving private source visibility and exact replay."""

import json
import subprocess
from hashlib import sha256
from pathlib import Path
from typing import cast

import pytest
from pydantic import JsonValue

from stock_research_llm_orchestrator.agents.configuration import load_agent_configuration
from stock_research_llm_orchestrator.agents.research_provider import WebTurnResult, invoke_research
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.acquisition_analysis import (
    AcquisitionRequest,
    AcquisitionResult,
)
from stock_research_llm_orchestrator.research.acquisition import replay_saved_bundle, run_workflow, validate_bundle
from stock_research_llm_orchestrator.research.document_summary import summary_request
from stock_research_llm_orchestrator.research.workflow import ResearchRequest

from .test_acquisition import INSTRUCTIONS, _acquire, _acquisition_turn, _final, _request
from .test_configured import PROFILE, ROOT
from .test_evidence_read import _document
from .test_workflow import _inputs


def _summary(request: ResearchRequest) -> WebTurnResult:
    """Return neutral navigation from document-only input, without an actual provider."""
    assert set(request.data) == {"raw_sha256", "text_sha256", "line_count", "numbered_text"}
    return WebTurnResult(
        json.dumps(
            {
                "raw_sha256": request.data["raw_sha256"],
                "text_sha256": request.data["text_sha256"],
                "overview": "A neutral filing describing its supplied disclosure.",
                "topics": ["disclosure"],
                "sections": [{"title": "Disclosure", "start_line": 1, "end_line": request.data["line_count"]}],
                "qualifications": [],
                "limitations": ["Extracted text only; images are not interpreted."],
            }
        ).encode(),
        b"",
    )


@pytest.fixture(scope="module")
def navigated_bundle() -> dict[str, bytes]:
    """A private acquisition is summarized once, read selectively, and later shared."""
    asked = read = False
    summary_calls: list[str] = []

    def invoke(request: ResearchRequest) -> WebTurnResult:
        nonlocal asked, read
        if request.stage.startswith("document_summary-"):
            summary_calls.append(request.stage)
            return _summary(request)
        if request.stage == "track_a_capital" and not asked:
            asked = True
            return _acquisition_turn()
        if request.stage != "translation":
            catalogs = cast(list[dict[str, JsonValue]], request.data["acquired_evidence"])
            if request.data["evidence_epoch"] == 1 and request.stage in {
                "track_a_income",
                "track_b_capital",
                "track_b_income",
            }:
                assert not catalogs
            if request.stage == "track_a_capital" and catalogs and not read:
                read = True
                assert "navigation_summary" in json.loads(cast(str, catalogs[0]["text"]))
                return WebTurnResult(
                    json.dumps(
                        {
                            "kind": "acquire",
                            "state": "Read the original disclosure line.",
                            "payload": None,
                            "requests": [
                                {
                                    "operation": "read_evidence",
                                    "evidence_id": "acquired-filing",
                                    "query": None,
                                    "start_line": 1,
                                    "end_line": 1,
                                }
                            ],
                        }
                    ).encode(),
                    b"",
                )
            if request.data["evidence_epoch"] == 2:
                assert "navigation_summary" in json.loads(cast(str, catalogs[0]["text"]))
        return _final(request)

    files = run_workflow(
        _inputs(),
        load_agent_configuration(PROFILE),
        invoke,
        INSTRUCTIONS,
        mode="replay",
        acquire=_acquire,
        execution_profile="parallel_incremental_v3",
        reassessment_from_epoch=2,
        stable_evidence_from_epoch=1,
        batch_common_updates_from_epoch=1,
    )
    assert read and len(summary_calls) == 1
    key = summary_calls[0]
    assert files[f"summaries/{key}/source.txt"] == b"A neutral original filing."
    assert files[f"summaries/{key}/source.md"]
    assert files[f"summaries/{key}/summary.md"]
    assert json.loads(files["document-summaries.json"])["acquired-filing"]["summary_key"] == key
    reads = [json.loads(value) for name, value in files.items() if name.startswith("evidence-reads/")]
    assert any(item.get("selected_lines") == [{"line": 1, "text": "A neutral original filing."}] for item in reads)
    return files


def test_navigation_reconstructs_and_replays_without_a_summarizer(navigated_bundle: dict[str, bytes]) -> None:
    """Summary calls use their own saved stages without advancing analysis role counters."""
    validate_bundle(navigated_bundle)
    assert replay_saved_bundle(navigated_bundle, _inputs(), load_agent_configuration(PROFILE)) == navigated_bundle


@pytest.mark.parametrize("suffix", ["summary.json", "summary.md", "source.txt", "source.md"])
def test_navigation_or_source_view_tampering_is_rejected(navigated_bundle: dict[str, bytes], suffix: str) -> None:
    """Stored navigation and complete derived reader views remain bound to the originals."""
    altered = dict(navigated_bundle)
    path = next(name for name in altered if name.startswith("summaries/") and name.endswith("/" + suffix))
    altered[path] += b"tampered"
    with pytest.raises(ValueError):
        validate_bundle(altered)


def test_identical_documents_share_summary_content_without_sharing_receipt_provenance() -> None:
    """Mirrored identical originals get one summary and retain their distinct current source URLs."""
    asked = False
    calls: list[str] = []

    def acquire(request: AcquisitionRequest, role: str, turn: int) -> tuple[AcquisitionResult, dict[str, bytes]]:
        result, files = _acquire(request, role, turn)
        assert request.url is not None
        evidence = result.evidence[0].model_copy(
            update={"evidence_id": "document-" + sha256(request.url.encode()).hexdigest()[:8]}
        )
        return result.model_copy(update={"evidence": (evidence,)}), files

    def invoke(request: ResearchRequest) -> WebTurnResult:
        nonlocal asked
        if request.stage.startswith("document_summary-"):
            calls.append(request.stage)
            return _summary(request)
        if request.stage == "track_a_capital" and not asked:
            asked = True
            requests = [_request().model_dump(mode="json"), _request().model_dump(mode="json")]
            requests[1]["url"] = "https://example.com/mirror"
            return WebTurnResult(
                json.dumps(
                    {"kind": "acquire", "requests": requests, "state": "Read originals.", "payload": None}
                ).encode(),
                b"",
            )
        return _final(request)

    files = run_workflow(
        _inputs(),
        load_agent_configuration(PROFILE),
        invoke,
        INSTRUCTIONS,
        mode="replay",
        acquire=acquire,
        execution_profile="parallel_incremental_v3",
        reassessment_from_epoch=2,
        stable_evidence_from_epoch=1,
        batch_common_updates_from_epoch=1,
    )
    index = json.loads(files["document-summaries.json"])
    assert len(calls) == 1 and len(index) == 2
    assert {item["source_reference"] for item in index.values()} == {
        "https://example.com/filing",
        "https://example.com/mirror",
    }
    assert len({item["summary_key"] for item in index.values()}) == 1


@pytest.mark.parametrize("provider", ["codex", "claude"])
def test_document_summary_provider_cannot_browse_or_call_external_tools(
    provider: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Summary contexts stay document-only at the transport boundary as well as in instructions."""
    config = load_agent_configuration(ROOT / "config/agent-configurations/cross-family/v1.yaml")
    request = summary_request(_document("Original text."), config.agents["gpt" if provider == "codex" else "claude"])
    body = json.loads(_summary(request).body)

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        if provider == "codex":
            assert 'web_search="disabled"' in command and 'web_search="live"' not in command
            Path(command[command.index("--output-last-message") + 1]).write_text(json.dumps(body))
            return subprocess.CompletedProcess(command, 0, b"", b"")
        assert command[command.index("--tools") + 1] == ""
        assert command[command.index("--allowedTools") + 1] == ""
        return subprocess.CompletedProcess(
            command,
            0,
            json.dumps({"type": "result", "subtype": "success", "is_error": False, "structured_output": body}).encode(),
            b"",
        )

    monkeypatch.setattr(subprocess, "run", run)
    assert json.loads(invoke_research(request)) == body
