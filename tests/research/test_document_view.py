"""Document reader views preserve originals and assert only verified PDF page spans."""

import io
import json
from hashlib import sha256

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from stock_research_llm_orchestrator.preparation.research_follow_up import extract_text
from stock_research_llm_orchestrator.research.document_view import document_view_files

from .test_evidence_read import _document


def _pdf(*pages: str) -> bytes:
    """Build actual text pages using the existing PDF extraction fixture pattern."""
    writer = PdfWriter()
    for text in pages:
        page = writer.add_blank_page(width=600, height=800)
        font = DictionaryObject(
            {NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1")}
        )
        font[NameObject("/BaseFont")] = NameObject("/Helvetica")
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
        )
        content = DecodedStreamObject()
        commands = ["BT /F1 12 Tf 30 750 Td"]
        for index, line in enumerate(text.splitlines()):
            if index:
                commands.append("0 -20 Td")
            commands.append(f"({line}) Tj")
        commands.append("ET")
        content.set_data("\n".join(commands).encode())
        page[NameObject("/Contents")] = content
    stream = io.BytesIO()
    writer.write(stream)
    return stream.getvalue()


def test_exact_text_and_line_numbers_survive_markdown_view_without_source_provenance() -> None:
    """Whitespace, blank lines, Unicode, and source fences stay tied to canonical lines."""
    text = " Title  \n\n売上高\n```\n[link](https://example.com)\n"
    item = _document(text)
    files = document_view_files("content", item, None, "text/html")
    assert files["summaries/content/source.txt"] == text.encode()
    markdown = files["summaries/content/source.md"].decode()
    assert "Derived source reader view" in markdown
    assert "Formatting does not reconstruct original headings, tables, charts" in markdown
    assert "No OCR" in markdown
    assert "````text\n1:  Title  \n2: \n3: 売上高\n4: ```\n5: [link](https://example.com)\n````" in markdown
    assert "summaries/content/page-map.json" not in files
    assert item.evidence_id not in markdown
    assert item.source_reference not in markdown
    changed = item.model_copy(update={"evidence_id": "another-id", "source_reference": "https://another.example"})
    assert document_view_files("content", changed, None, "text/html") == files


def test_pdf_page_boundaries_bind_to_unchanged_canonical_lines() -> None:
    """Physical pages receive inclusive canonical ranges only after exact extraction equality."""
    raw = _pdf("First page\nSecond line", "Last page")
    text = extract_text(raw, "application/pdf")
    files = document_view_files("pdf", _document(text), raw, "application/pdf")
    assert files["summaries/pdf/source.txt"] == text.encode()
    metadata = json.loads(files["summaries/pdf/page-map.json"])
    assert metadata["status"] == "mapped"
    assert metadata["text_sha256"] == sha256(text.encode()).hexdigest()
    assert metadata["line_count"] == 3
    assert metadata["pages"] == [
        {"physical_page": 1, "start_line": 1, "end_line": 2},
        {"physical_page": 2, "start_line": 3, "end_line": 3},
    ]


def test_empty_pdf_page_has_no_invented_line_span_or_ocr_text() -> None:
    """A blank page retains its physical position while producing no canonical content."""
    raw = _pdf("First page", "", "Last page")
    text = extract_text(raw, "application/pdf")
    files = document_view_files("pdf", _document(text), raw, "application/pdf")
    metadata = json.loads(files["summaries/pdf/page-map.json"])
    assert metadata["status"] == "mapped"
    assert metadata["pages"][1] == {"physical_page": 2, "start_line": None, "end_line": None}
    assert metadata["pages"][2] == {"physical_page": 3, "start_line": 2, "end_line": 2}
    assert "Physical PDF page 2 has no extractable text" in metadata["limitations"][0]
    assert "Physical PDF page 2 has no extractable text" in files["summaries/pdf/source.md"].decode()


def test_mismatched_pdf_extraction_never_asserts_page_spans() -> None:
    """A canonical source with an extra newline cannot borrow spans from a similar PDF."""
    raw = _pdf("Original page")
    text = extract_text(raw, "application/pdf") + "\n"
    files = document_view_files("pdf", _document(text), raw, "application/pdf")
    metadata = json.loads(files["summaries/pdf/page-map.json"])
    assert metadata["status"] == "mismatch"
    assert metadata["pages"] == []
    assert "does not exactly match" in metadata["limitations"][0]
    assert files["summaries/pdf/source.txt"] == text.encode()


def test_missing_or_invalid_pdf_bytes_keep_reader_view_without_page_claims() -> None:
    """Optional extraction failures disclose unavailable mapping and preserve supplied source text."""
    for raw in (None, b"not a PDF"):
        files = document_view_files("pdf", _document("Retained text"), raw, "application/pdf")
        metadata = json.loads(files["summaries/pdf/page-map.json"])
        assert metadata["status"] == "unavailable"
        assert metadata["pages"] == []
        assert metadata["limitations"]
        assert "Physical PDF page mapping status: unavailable" in files["summaries/pdf/source.md"].decode()
        assert files["summaries/pdf/source.txt"] == b"Retained text"
