"""Tests for the pure EDINET XBRL ZIP retrieval adapter."""

import hashlib
import io
import stat
import zipfile

import pytest

from stock_research_llm_orchestrator.sources import (
    BoundedSourceResponse,
    EdinetDocumentRetrievalAdapter,
    EdinetDocumentRetrievalParseError,
    SourceParameter,
)


def _zip_bytes(entries: tuple[tuple[str, bytes], ...]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, body in entries:
            archive.writestr(path, body)
    return output.getvalue()


def _response(body: bytes, *, media_type: str = "application/zip", encoding: str = "binary") -> BoundedSourceResponse:
    return BoundedSourceResponse(
        physical_attempt_id="attempt-1",
        body=body,
        sha256=hashlib.sha256(body).hexdigest(),
        media_type=media_type,
        encoding=encoding,
    )


def test_build_intent_uses_document_id_as_transport_resource() -> None:
    """Keep the retrieval type non-secret while routing by the document ID."""
    intent = EdinetDocumentRetrievalAdapter().build_intent(
        "document-retrieval",
        (SourceParameter(name="document_id", value="SYNTHETIC001"), SourceParameter(name="type", value="1")),
    )

    assert intent.resource_key == "SYNTHETIC001"
    assert intent.origin == "api.edinet-fsa.go.jp"
    assert intent.parameters[1].value == "1"


@pytest.mark.parametrize(
    ("operation", "parameters"),
    [
        ("document-list", (SourceParameter(name="document_id", value="SYNTHETIC001"),)),
        (
            "document-retrieval",
            (SourceParameter(name="document_id", value="SYNTHETIC001"), SourceParameter(name="type", value="2")),
        ),
        (
            "document-retrieval",
            (SourceParameter(name="document_id", value="unsafe/path"), SourceParameter(name="type", value="1")),
        ),
    ],
)
def test_build_intent_rejects_unapproved_retrieval_input(
    operation: str, parameters: tuple[SourceParameter, ...]
) -> None:
    """Reject operations, retrieval types, and document IDs outside the mapping."""
    with pytest.raises(ValueError):
        EdinetDocumentRetrievalAdapter().build_intent(operation, parameters)


def test_parse_inventories_xbrl_zip_without_extracting() -> None:
    """Return safe metadata for artificial XBRL and manifest members."""
    body = _zip_bytes(
        (
            ("XBRL/PublicDoc/synthetic.xbrl", b"<xbrl>synthetic</xbrl>"),
            ("XBRL/PublicDoc/manifest.xml", b"<manifest />"),
        )
    )

    parsed = EdinetDocumentRetrievalAdapter().parse(_response(body))

    assert parsed.archive_sha256 == hashlib.sha256(body).hexdigest()
    assert len(parsed.members) == 2
    assert tuple(member.is_xbrl for member in parsed.members) == (True, False)
    assert parsed.total_uncompressed_bytes == 34


@pytest.mark.parametrize("body", [b"not-a-zip", b""])
def test_parse_rejects_invalid_zip_without_body_leak(body: bytes) -> None:
    """Expose one sanitized error for malformed archive bytes."""
    with pytest.raises(EdinetDocumentRetrievalParseError, match="^edinet_document_retrieval_invalid$") as exc_info:
        EdinetDocumentRetrievalAdapter().parse(_response(body))
    if body:
        assert body.decode(errors="ignore") not in str(exc_info.value)


@pytest.mark.parametrize(
    "path",
    ["../escape.xbrl", "/absolute.xbrl", "C:/drive.xbrl", "dir\\file.xbrl", "dir//file.xbrl", "."],
)
def test_parse_rejects_unsafe_member_paths(path: str) -> None:
    """Reject path traversal and platform-specific absolute paths before extraction."""
    with pytest.raises(EdinetDocumentRetrievalParseError, match="^edinet_document_retrieval_invalid$"):
        EdinetDocumentRetrievalAdapter().parse(_response(_zip_bytes(((path, b"synthetic"),))))


def test_parse_rejects_unsafe_directory_entries() -> None:
    """Inspect directory paths even though directories are absent from the result inventory."""
    body = _zip_bytes((("../", b""), ("safe.xbrl", b"<xbrl />")))
    with pytest.raises(EdinetDocumentRetrievalParseError, match="^edinet_document_retrieval_invalid$"):
        EdinetDocumentRetrievalAdapter().parse(_response(body))


def test_parse_rejects_duplicate_member_paths() -> None:
    """Reject ambiguous archive members rather than selecting one by order."""
    output = io.BytesIO()
    with zipfile.ZipFile(output, mode="w") as archive:
        archive.writestr("duplicate.xbrl", b"first")
        with pytest.warns(UserWarning, match="Duplicate name"):
            archive.writestr("duplicate.xbrl", b"second")

    with pytest.raises(EdinetDocumentRetrievalParseError, match="^edinet_document_retrieval_invalid$"):
        EdinetDocumentRetrievalAdapter().parse(_response(output.getvalue()))


def test_parse_rejects_symlink_members() -> None:
    """Reject Unix symlink metadata even though no member is extracted."""
    output = io.BytesIO()
    info = zipfile.ZipInfo("link.xbrl")
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(output, mode="w") as archive:
        archive.writestr(info, b"target")

    with pytest.raises(EdinetDocumentRetrievalParseError, match="^edinet_document_retrieval_invalid$"):
        EdinetDocumentRetrievalAdapter().parse(_response(output.getvalue()))


def test_parse_requires_at_least_one_xbrl_member() -> None:
    """Fail closed when the retrieval is a ZIP but not an XBRL document archive."""
    with pytest.raises(EdinetDocumentRetrievalParseError, match="^edinet_document_retrieval_invalid$"):
        EdinetDocumentRetrievalAdapter().parse(_response(_zip_bytes((("manifest.xml", b"<manifest />"),))))


@pytest.mark.parametrize(
    ("media_type", "encoding"),
    [("application/octet-stream", "binary"), ("application/zip", "utf-8")],
)
def test_parse_rejects_unapproved_transport_metadata(media_type: str, encoding: str) -> None:
    """Require the approved EDINET ZIP media type and binary encoding marker."""
    body = _zip_bytes((("synthetic.xbrl", b"<xbrl />"),))
    with pytest.raises(EdinetDocumentRetrievalParseError, match="^edinet_document_retrieval_invalid$"):
        EdinetDocumentRetrievalAdapter().parse(_response(body, media_type=media_type, encoding=encoding))
