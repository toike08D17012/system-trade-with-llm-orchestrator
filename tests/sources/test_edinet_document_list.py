"""Tests for the pure EDINET document-list adapter."""

import hashlib
import json
from pathlib import Path

import pytest

from stock_research_llm_orchestrator.sources import (
    BoundedSourceResponse,
    EdinetDocumentListAdapter,
    EdinetDocumentListParseError,
    EdinetDocumentType,
    SourceParameter,
)


FIXTURE = Path(__file__).parents[1] / "fixtures/sources/edinet/document-list.json"


def _response(body: bytes, *, media_type: str = "application/json", encoding: str = "utf-8") -> BoundedSourceResponse:
    return BoundedSourceResponse(
        physical_attempt_id="attempt-1",
        body=body,
        sha256=hashlib.sha256(body).hexdigest(),
        media_type=media_type,
        encoding=encoding,
    )


def test_build_intent_is_canonical_and_credential_free() -> None:
    """Persist only the documented date and detail-type parameters."""
    intent = EdinetDocumentListAdapter().build_intent(
        "document-list",
        (SourceParameter(name="date", value="2026-09-22"), SourceParameter(name="type", value="2")),
    )

    assert intent.model_dump() == {
        "source_id": "edinet",
        "operation": "document-list",
        "origin": "api.edinet-fsa.go.jp",
        "resource_key": "api-v2-documents",
        "parameters": ({"name": "date", "value": "2026-09-22"}, {"name": "type", "value": "2"}),
    }


@pytest.mark.parametrize(
    ("operation", "parameters"),
    [
        ("document-retrieval", (SourceParameter(name="date", value="2026-09-22"),)),
        ("document-list", (SourceParameter(name="date", value="not-a-date"), SourceParameter(name="type", value="2"))),
        ("document-list", (SourceParameter(name="date", value="2026-09-22"), SourceParameter(name="type", value="1"))),
    ],
)
def test_build_intent_rejects_unsupported_or_noncanonical_input(
    operation: str, parameters: tuple[SourceParameter, ...]
) -> None:
    """Reject operation and parameter forms outside the approved mapping."""
    with pytest.raises(ValueError):
        EdinetDocumentListAdapter().build_intent(operation, parameters)


def test_parse_selects_only_approved_document_types() -> None:
    """Validate the full provider shape while retaining approved filing types."""
    parsed = EdinetDocumentListAdapter().parse(_response(FIXTURE.read_bytes()))

    assert parsed.requested_date == "2026-09-22"
    assert parsed.provider_result_count == 2
    assert len(parsed.documents) == 1
    assert parsed.documents[0].document_id == "SYNTHETIC001"
    assert parsed.documents[0].document_type is EdinetDocumentType.ANNUAL_REPORT
    assert parsed.documents[0].xbrl_available is True


def test_parse_accepts_an_empty_document_list() -> None:
    """Represent an authentic empty day without inventing documents."""
    value = json.loads(FIXTURE.read_bytes())
    value["metadata"]["resultset"]["count"] = 0
    value["results"] = []
    parsed = EdinetDocumentListAdapter().parse(_response(json.dumps(value).encode()))

    assert parsed.provider_result_count == 0
    assert parsed.documents == ()


def test_parse_preserves_an_amendment_parent_relationship() -> None:
    """Represent an amendment separately and retain its original document ID."""
    value = json.loads(FIXTURE.read_bytes())
    value["metadata"]["resultset"]["count"] = 1
    value["results"] = [value["results"][0]]
    value["results"][0]["docTypeCode"] = "130"
    value["results"][0]["parentDocID"] = "SYNTHETIC000"

    parsed = EdinetDocumentListAdapter().parse(_response(json.dumps(value).encode()))

    assert parsed.documents[0].document_type is EdinetDocumentType.AMENDED_ANNUAL_REPORT
    assert parsed.documents[0].parent_document_id == "SYNTHETIC000"


@pytest.mark.parametrize(
    "body",
    [
        b"not-json",
        b'{"metadata":null,"results":[]}',
        b'{"metadata":{},"metadata":{},"results":[]}',
    ],
)
def test_parse_rejects_malformed_null_and_duplicate_keys_without_body_leak(body: bytes) -> None:
    """Return one sanitized failure for structurally unsafe JSON."""
    with pytest.raises(EdinetDocumentListParseError, match="^edinet_document_list_invalid$") as exc_info:
        EdinetDocumentListAdapter().parse(_response(body))
    assert body.decode(errors="ignore") not in str(exc_info.value)


def test_parse_rejects_duplicate_approved_document_ids() -> None:
    """Reject ambiguous correction or duplicate rows instead of overwriting."""
    value = json.loads(FIXTURE.read_bytes())
    duplicate = dict(value["results"][0])
    duplicate["seqNumber"] = 2
    value["results"] = [value["results"][0], duplicate]
    value["metadata"]["resultset"]["count"] = 2

    with pytest.raises(EdinetDocumentListParseError, match="^edinet_document_list_invalid$"):
        EdinetDocumentListAdapter().parse(_response(json.dumps(value).encode()))


@pytest.mark.parametrize(
    ("media_type", "encoding"),
    [("text/json", "utf-8"), ("application/json", "shift_jis")],
)
def test_parse_rejects_unapproved_transport_metadata(media_type: str, encoding: str) -> None:
    """Require the approved EDINET list media type and encoding."""
    with pytest.raises(EdinetDocumentListParseError, match="^edinet_document_list_invalid$"):
        EdinetDocumentListAdapter().parse(_response(FIXTURE.read_bytes(), media_type=media_type, encoding=encoding))
