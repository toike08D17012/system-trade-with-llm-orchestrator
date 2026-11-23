"""Retained document reading is explicit, bounded by requested lines, and reproducible."""

import json
from hashlib import sha256

from stock_research_llm_orchestrator.contracts.base import FileReferenceV1
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.acquisition_analysis import (
    AcquiredEvidence,
    EvidenceReadRequest,
)
from stock_research_llm_orchestrator.research.evidence_read import evidence_catalog, read_evidence


def _document(text: str = "Title\n Cash FLOWS  \n売上高\nEnd") -> AcquiredEvidence:
    return AcquiredEvidence(
        evidence_id="issuer-document",
        source_id="issuer-ir",
        source_reference="https://example.com/annual.pdf",
        retrieved_at="2026-11-15T00:00:00Z",
        raw_reference=FileReferenceV1(
            artifact_id="original", relative_path="raw.pdf", media_type="application/pdf", sha256="a" * 64
        ),
        text=text,
    )


def test_catalog_preserves_full_source_identity_without_inserting_large_document_body() -> None:
    """The model sees an explicit catalog, while the retained evidence is unchanged."""
    text = "Annual statement\n" + "Full original line\n" * 100_000
    item = _document(text)
    excerpt = evidence_catalog(item)
    catalog = json.loads(str(excerpt["text"]))
    assert catalog["kind"] == "retained_document_catalog"
    assert catalog["line_count"] == 100_001
    assert catalog["text_sha256"] == sha256(text.encode()).hexdigest()
    assert "Full original line" not in str(excerpt)
    assert item.text == text
    assert excerpt["raw_reference"] == item.raw_reference.model_dump(mode="json")


def test_search_and_read_preserve_exact_line_positions_whitespace_and_unicode() -> None:
    """Search locates content; a separate requested range supplies the actual source text."""
    item = _document()
    visible = {item.evidence_id: item}
    search = read_evidence(
        EvidenceReadRequest(operation="search_evidence", evidence_id=item.evidence_id, query="cash flows"), visible
    )
    assert search["matching_lines"] == [2]
    read = read_evidence(
        EvidenceReadRequest(operation="read_evidence", evidence_id=item.evidence_id, start_line=2, end_line=3), visible
    )
    assert read["selected_lines"] == [{"line": 2, "text": " Cash FLOWS  "}, {"line": 3, "text": "売上高"}]
    assert search["source_text_sha256"] == read["source_text_sha256"]
    assert read["line_count"] == 4


def test_reader_rejects_unknown_evidence_and_out_of_range_without_silently_clipping() -> None:
    """A role cannot read a peer-only document or mistake a partial range for a complete read."""
    request = EvidenceReadRequest(operation="read_evidence", evidence_id="issuer-document", start_line=2, end_line=10)
    assert read_evidence(request, {})["status"] == "not_available"
    result = read_evidence(request, {"issuer-document": _document()})
    assert result["status"] == "invalid_range"
    assert "selected_lines" not in result


def test_search_has_no_synthetic_match_count_cap() -> None:
    """All actual matching line indexes remain available for model-selected subsequent reads."""
    item = _document("needle\n" * 2_000)
    result = read_evidence(
        EvidenceReadRequest(operation="search_evidence", evidence_id=item.evidence_id, query="needle"),
        {item.evidence_id: item},
    )
    assert result["matching_lines"] == list(range(1, 2_001))


def test_document_reader_preserves_native_market_transfer_boundary() -> None:
    """A text read cannot bypass the configured summary-only native price transfer."""
    item = _document().model_copy(update={"source_id": "yfinance"})
    result = read_evidence(
        EvidenceReadRequest(operation="read_evidence", evidence_id=item.evidence_id, start_line=1, end_line=4),
        {item.evidence_id: item},
    )
    assert result["status"] == "not_available"
    assert "selected_lines" not in result
