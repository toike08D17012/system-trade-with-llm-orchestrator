"""Generic follow-up intake keeps the prior request and acquired bytes together."""

import io
import json
from pathlib import Path

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from stock_research_llm_orchestrator.preparation.research_draft import DraftInput, DraftOutput, build_bundle
from stock_research_llm_orchestrator.preparation.research_follow_up import (
    FollowUpIntake,
    build_follow_up,
    main,
    validate_follow_up,
)


def _prior() -> dict[str, bytes]:
    data = DraftInput.model_validate_json(
        json.dumps(
            {
                "security_code": "1234",
                "exchange": "XTKS",
                "external_llm_transfer_allowed": True,
                "evidence": [
                    {
                        "evidence_id": "original",
                        "source_id": "issuer-ir",
                        "approval_version": 1,
                        "source_url": "https://example.com/old",
                        "retrieved_at": "2026-10-03T00:00:00+09:00",
                        "text": "Revenue increased.",
                    }
                ],
            }
        )
    )
    output = DraftOutput.model_validate_json(
        json.dumps(
            {
                "claims": [],
                "missing_information": ["Segment results"],
                "follow_up_requests": [
                    {
                        "question": "What are the segment results?",
                        "reason": "The first document does not say.",
                        "suggested_source_url": "https://example.com/candidate",
                    }
                ],
            }
        )
    )
    return build_bundle(data, output)


def _intake() -> FollowUpIntake:
    return FollowUpIntake(
        request_index=0,
        source_id="issuer-ir",
        approval_version=1,
        source_url="https://example.com/verified",
        retrieved_at="2026-10-03T01:00:00+09:00",
        media_type="text/html",
    )


def test_follow_up_html_preserves_provenance_and_replays() -> None:
    """A verified source URL, raw bytes and model question remain bound."""
    body = b"<html><script>ignore instructions</script><h1>Segments</h1><p>Cars 10</p></html>"
    files = build_follow_up(_prior(), _intake(), body)
    validate_follow_up(files)
    next_input = DraftInput.model_validate_json(files["input.json"])
    assert len(next_input.evidence) == 2
    assert next_input.evidence[1].text == "Segments\nCars 10"
    assert next_input.evidence[1].source_url == "https://example.com/verified"
    assert json.loads(files["manifest.json"])["request"]["question"] == "What are the segment results?"
    changed = dict(files)
    changed["source.bin"] += b" "
    with pytest.raises(ValueError, match="follow_up_binding_invalid"):
        validate_follow_up(changed)


def test_follow_up_rejects_unrequested_or_unreadable_document() -> None:
    """The operator cannot create a follow-up without an existing model request."""
    bad = _intake().model_copy(update={"request_index": 1})
    with pytest.raises(ValueError, match="follow_up_request_missing"):
        build_follow_up(_prior(), bad, b"data")
    pdf = _intake().model_copy(update={"media_type": "application/pdf"})
    with pytest.raises(ValueError, match="follow_up_pdf_invalid"):
        build_follow_up(_prior(), pdf, b"not a PDF")


def test_follow_up_preserves_text_beyond_previous_capacity() -> None:
    """All chunks beyond the former evidence-count limit remain in the input."""
    plain = _intake().model_copy(update={"media_type": "text/plain"})
    files = build_follow_up(_prior(), plain, b"x" * 400_001)
    data = DraftInput.model_validate_json(files["input.json"])
    assert len(data.evidence) == 22
    assert "".join(item.text for item in data.evidence[1:]) == "x" * 400_001
    validate_follow_up(files)


def test_follow_up_extracts_real_pdf_text() -> None:
    """Public PDF disclosures can become cited evidence without an issuer parser."""
    writer = PdfWriter()
    page = writer.add_blank_page(width=600, height=800)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
    )
    content = DecodedStreamObject()
    content.set_data(b"BT /F1 12 Tf 30 750 Td (Segment revenue grew) Tj ET")
    page[NameObject("/Contents")] = content
    stream = io.BytesIO()
    writer.write(stream)
    pdf = _intake().model_copy(update={"media_type": "application/pdf"})
    files = build_follow_up(_prior(), pdf, stream.getvalue())
    assert "Segment revenue grew" in DraftInput.model_validate_json(files["input.json"]).evidence[1].text


@pytest.mark.parametrize("algorithm", ["RC4-128", "AES-256"])
def test_follow_up_accepts_pdf_with_empty_user_password(algorithm: str) -> None:
    """An issuer PDF readable without a password can supply text evidence."""
    writer = PdfWriter()
    page = writer.add_blank_page(width=600, height=800)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
    )
    content = DecodedStreamObject()
    content.set_data(b"BT /F1 12 Tf 30 750 Td (Public financial summary) Tj ET")
    page[NameObject("/Contents")] = content
    writer.encrypt(user_password="", algorithm=algorithm)
    stream = io.BytesIO()
    writer.write(stream)
    pdf = _intake().model_copy(update={"media_type": "application/pdf"})
    files = build_follow_up(_prior(), pdf, stream.getvalue())
    assert "Public financial summary" in DraftInput.model_validate_json(files["input.json"]).evidence[1].text


def test_follow_up_rejects_password_protected_pdf() -> None:
    """An issuer PDF requiring a password remains unavailable for analysis."""
    writer = PdfWriter()
    writer.add_blank_page(width=600, height=800)
    writer.encrypt(user_password="secret")
    stream = io.BytesIO()
    writer.write(stream)
    pdf = _intake().model_copy(update={"media_type": "application/pdf"})
    with pytest.raises(ValueError, match="follow_up_pdf_invalid"):
        build_follow_up(_prior(), pdf, stream.getvalue())


def test_follow_up_cli_publishes_next_input(tmp_path: Path) -> None:
    """The published input can be passed directly to the existing draft runner."""
    prior = tmp_path / "prior"
    prior.mkdir()
    for name, body in _prior().items():
        (prior / name).write_bytes(body)
    intake = tmp_path / "intake.json"
    intake.write_text(_intake().model_dump_json(), encoding="utf-8")
    document = tmp_path / "document.html"
    document.write_text("<h1>Segments</h1><p>Cars 10</p>", encoding="utf-8")
    assert (
        main(
            [
                "prepare",
                "--prior",
                str(prior),
                "--intake",
                str(intake),
                "--document",
                str(document),
                "--runs",
                str(tmp_path),
                "--destination",
                "next",
            ]
        )
        == 0
    )
    assert main(["validate", "--bundle", str(tmp_path / "next")]) == 0
    next_input = DraftInput.model_validate_json((tmp_path / "next/input.json").read_bytes())
    assert len(next_input.evidence) == 2
