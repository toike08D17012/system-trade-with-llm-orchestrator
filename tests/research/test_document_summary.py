"""Verify isolated content-bound document navigation and retained provenance."""

import json
from hashlib import sha256
from typing import cast

import pytest
from pydantic import ValidationError

from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.agent_configuration import (
    AgentDefinition,
    ReasoningSettings,
)
from stock_research_llm_orchestrator.research import document_summary
from stock_research_llm_orchestrator.research.document_summary import (
    DocumentSection,
    DocumentSummary,
    summary_catalog,
    summary_files,
    summary_request,
    validate_summary_response,
)
from stock_research_llm_orchestrator.research.workflow import ResearchRequest

from .test_evidence_read import _document


@pytest.fixture
def agent() -> AgentDefinition:
    """Use explicit settings without invoking the selected provider."""
    return AgentDefinition(
        adapter="codex_cli",
        model="gpt-6.2-sol",
        model_family="openai_gpt",
        reasoning=ReasoningSettings(effort="medium"),
    )


def _response(request: ResearchRequest) -> bytes:
    return json.dumps(
        {
            "raw_sha256": request.data["raw_sha256"],
            "text_sha256": request.data["text_sha256"],
            "overview": "An issuer document with financial and qualification sections.",
            "topics": ["Financial disclosures", "Qualifications"],
            "sections": [{"title": "Document contents", "start_line": 1, "end_line": request.data["line_count"]}],
            "qualifications": [{"title": "Qualifications to inspect", "start_line": 2, "end_line": 2}],
            "limitations": ["Text extraction alone does not establish table layout fidelity."],
        }
    ).encode()


def test_summary_request_transfers_all_lines_without_private_context_or_receipt_identity(
    agent: AgentDefinition,
) -> None:
    """The summarizer receives complete numbered extraction in a separate context."""
    text = "Document title\n  Qualified original passage  \n\n売上高\nFinal original line\n"
    item = _document(text)
    request = summary_request(item, agent)
    assert request.provider == "codex" and request.agent == agent
    assert request.response_model is DocumentSummary
    assert request.stage.startswith("document_summary-") and len(request.stage) == len("document_summary-") + 64
    assert set(request.data) == {"raw_sha256", "text_sha256", "line_count", "numbered_text"}
    assert request.data["numbered_text"] == (
        "1: Document title\n2:   Qualified original passage  \n3: \n4: 売上高\n5: Final original line"
    )
    assert request.data["line_count"] == 5
    assert request.data["text_sha256"] == sha256(text.encode()).hexdigest()
    assert item.source_reference not in json.dumps(request.data)
    assert item.evidence_id not in json.dumps(request.data)
    assert "Do not\nbrowse the Web" in request.instructions
    assert "Do not recommend investments" in request.instructions
    assert "not\nfinancial evidence" in request.instructions


def test_identical_content_reuses_summary_identity_despite_different_receipt(agent: AgentDefinition) -> None:
    """URL, retrieval time, evidence ID and artifact path do not trigger a new summary."""
    original = _document()
    receipt = original.model_copy(
        update={
            "evidence_id": "another-document-id",
            "source_reference": "https://example.org/mirror.html",
            "retrieved_at": "2026-11-22T00:00:00Z",
            "raw_reference": original.raw_reference.model_copy(
                update={"artifact_id": "different-original", "relative_path": "mirror/source.pdf"}
            ),
        }
    )
    assert summary_request(original, agent) == summary_request(receipt, agent)


@pytest.mark.parametrize("change", ["text", "raw", "model", "effort", "provider", "instructions", "schema"])
def test_summary_identity_changes_with_content_or_generation_settings(
    agent: AgentDefinition, change: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stale content or model settings never reuse a previous generation key."""
    item = _document()
    before = summary_request(item, agent)
    if change == "text":
        item = item.model_copy(update={"text": item.text + "\nRevised disclosure"})
    elif change == "raw":
        item = item.model_copy(update={"raw_reference": item.raw_reference.model_copy(update={"sha256": "b" * 64})})
    elif change == "model":
        agent = agent.model_copy(update={"model": "gpt-6.1-terra"})
    elif change == "effort":
        agent = agent.model_copy(update={"reasoning": ReasoningSettings(effort="low")})
    elif change == "provider":
        agent = AgentDefinition(
            adapter="claude_cli",
            model="claude-sonnet-4-5",
            model_family="anthropic_claude",
            reasoning=ReasoningSettings(effort="medium"),
        )
    elif change == "instructions":
        monkeypatch.setattr(
            document_summary, "SUMMARY_INSTRUCTIONS", before.instructions + "\nRevised navigation rules."
        )
    else:
        schema = DocumentSummary.model_json_schema()
        schema["description"] = "Revised schema for content-bound navigation."
        monkeypatch.setattr(DocumentSummary, "model_json_schema", classmethod(lambda _cls: schema))
    assert summary_request(item, agent).stage != before.stage


def test_validate_summary_binds_original_text_and_every_section_range(agent: AgentDefinition) -> None:
    """Validated navigation addresses both topic sections and qualifications."""
    request = summary_request(_document(), agent)
    result = validate_summary_response(request, _response(request))
    assert result.raw_sha256 == request.data["raw_sha256"]
    assert result.text_sha256 == request.data["text_sha256"]
    assert result.sections[0].end_line == request.data["line_count"]
    assert result.qualifications[0].start_line == 2


@pytest.mark.parametrize("field", ["raw_sha256", "text_sha256"])
def test_summary_rejects_mismatched_hashes(agent: AgentDefinition, field: str) -> None:
    """A cache entry for another original or extraction is not accepted."""
    request = summary_request(_document(), agent)
    response = json.loads(_response(request))
    response[field] = "0" * 64
    with pytest.raises(ValueError, match="document_summary_source_mismatch"):
        validate_summary_response(request, json.dumps(response).encode())


@pytest.mark.parametrize("field", ["sections", "qualifications"])
def test_summary_rejects_out_of_bounds_sections_and_qualifications(agent: AgentDefinition, field: str) -> None:
    """Neither navigation list can claim lines outside the retained extraction."""
    request = summary_request(_document(), agent)
    response = json.loads(_response(request))
    response[field][0]["end_line"] = 1000
    with pytest.raises(ValueError, match="document_summary_range_out_of_bounds"):
        validate_summary_response(request, json.dumps(response).encode())


@pytest.mark.parametrize(("start", "end"), [(0, 1), (2, 1)])
def test_summary_section_rejects_nonpositive_or_reversed_ranges(start: int, end: int) -> None:
    """Invalid ranges are not clipped into apparently valid navigation."""
    with pytest.raises(ValidationError):
        DocumentSection(title="Invalid section", start_line=start, end_line=end)


def test_summary_catalog_reattaches_current_provenance_and_preserves_original_access(agent: AgentDefinition) -> None:
    """Shared navigation does not copy an older receipt or become factual evidence."""
    original = _document()
    request = summary_request(original, agent)
    summary = validate_summary_response(request, _response(request))
    receipt = original.model_copy(
        update={
            "evidence_id": "mirror-document",
            "source_reference": "https://example.org/mirror.pdf",
            "retrieved_at": "2026-11-22T00:00:00Z",
        }
    )
    result = summary_catalog(receipt, summary, request.stage)
    assert result["evidence_id"] == receipt.evidence_id
    assert result["source_reference"] == receipt.source_reference
    assert result["retrieved_at"] == receipt.retrieved_at
    assert result["raw_reference"] == receipt.raw_reference.model_dump(mode="json")
    catalog = json.loads(cast(str, result["text"]))
    assert catalog["kind"] == "retained_document_catalog"
    assert catalog["available_operations"] == ["search_evidence", "read_evidence"]
    assert catalog["navigation_summary"] == summary.model_dump(mode="json")
    assert "navigation only, not financial evidence" in catalog["note"]
    assert "extracted text only" in catalog["note"]
    assert "PDF figures, images or complex tables may be absent" in catalog["note"]
    assert "omitted information is absent" in catalog["note"]
    assert catalog["files"]["source_text"] == f"summaries/{request.stage}/source.txt"
    assert catalog["files"]["source_markdown"] == f"summaries/{request.stage}/source.md"
    assert original.text == receipt.text


def test_summary_catalog_rejects_another_extraction(agent: AgentDefinition) -> None:
    """Applying an otherwise valid summary to revised text cannot hide the change."""
    original = _document()
    request = summary_request(original, agent)
    summary = validate_summary_response(request, _response(request))
    revised = original.model_copy(update={"text": original.text + "\nMaterial revision"})
    with pytest.raises(ValueError, match="document_summary_source_mismatch"):
        summary_catalog(revised, summary, request.stage)


def test_summary_files_keep_navigation_separate_and_escape_generated_markdown(agent: AgentDefinition) -> None:
    """Derived navigation preserves its hashes and cannot inject raw markup."""
    request = summary_request(_document(), agent)
    summary = validate_summary_response(request, _response(request)).model_copy(
        update={"overview": "<script>Untrusted</script>\n# Generated heading"}
    )
    files = summary_files(request.stage, summary)
    assert set(files) == {f"summaries/{request.stage}/summary.json", f"summaries/{request.stage}/summary.md"}
    assert DocumentSummary.model_validate_json(files[f"summaries/{request.stage}/summary.json"]) == summary
    markdown = files[f"summaries/{request.stage}/summary.md"].decode()
    assert "<script>" not in markdown
    assert "&lt;script&gt;" in markdown
    assert "original lines 1–4" in markdown
    assert summary.raw_sha256 in markdown and summary.text_sha256 in markdown


@pytest.mark.parametrize("key", ["../outside", "document_summary-" + "g" * 64, "document_summary-short"])
def test_summary_files_reject_non_content_addressed_paths(agent: AgentDefinition, key: str) -> None:
    """Derived files cannot escape their dedicated content-key directory."""
    request = summary_request(_document(), agent)
    summary = validate_summary_response(request, _response(request))
    with pytest.raises(ValueError, match="document_summary_key_invalid"):
        summary_files(key, summary)


def test_summary_rejects_an_empty_extraction_instead_of_claiming_it_was_read(agent: AgentDefinition) -> None:
    """An empty extraction requires an explicit limitation outside summarization."""
    with pytest.raises(ValueError, match="document_summary_text_empty"):
        summary_request(_document(" \n\t"), agent)
