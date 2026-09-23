"""Tests for bounded source-native EDINET XBRL fact extraction."""

import hashlib
import io
import zipfile

import pytest

from stock_research_llm_orchestrator.sources import (
    BoundedSourceResponse,
    EdinetDocumentArchive,
    EdinetDocumentRetrievalAdapter,
    EdinetXbrlFactExtractor,
    EdinetXbrlFactParseError,
)


XBRL = b"""<?xml version="1.0" encoding="UTF-8"?>
<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance"
             xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
             xmlns:jp="https://example.invalid/synthetic">
  <xbrli:context id="CurrentYear"><xbrli:entity /></xbrli:context>
  <xbrli:unit id="JPY"><xbrli:measure>iso4217:JPY</xbrli:measure></xbrli:unit>
  <jp:Revenue contextRef="CurrentYear" unitRef="JPY" decimals="-6">123000000</jp:Revenue>
  <jp:Profit contextRef="CurrentYear" unitRef="JPY" xsi:nil="true" />
</xbrli:xbrl>
"""


def _zip_response(xbrl: bytes = XBRL, *, path: str = "XBRL/PublicDoc/synthetic.xbrl") -> BoundedSourceResponse:
    output = io.BytesIO()
    with zipfile.ZipFile(output, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(path, xbrl)
    body = output.getvalue()
    return BoundedSourceResponse(
        physical_attempt_id="attempt-1",
        body=body,
        sha256=hashlib.sha256(body).hexdigest(),
        media_type="application/zip",
        encoding="binary",
    )


def _inventory(response: BoundedSourceResponse) -> EdinetDocumentArchive:
    return EdinetDocumentRetrievalAdapter().parse(response)


def test_extract_preserves_source_native_fact_semantics() -> None:
    """Keep QName, references, accuracy attributes, lexical value, and nil distinct."""
    response = _zip_response()
    extracted = EdinetXbrlFactExtractor().extract(response, _inventory(response))

    assert extracted.archive_sha256 == response.sha256
    assert extracted.xbrl_member_paths == ("XBRL/PublicDoc/synthetic.xbrl",)
    assert len(extracted.facts) == 2
    revenue, profit = extracted.facts
    assert revenue.model_dump() == {
        "ordinal": 1,
        "source_member_path": "XBRL/PublicDoc/synthetic.xbrl",
        "concept_namespace": "https://example.invalid/synthetic",
        "concept_local_name": "Revenue",
        "context_ref": "CurrentYear",
        "unit_ref": "JPY",
        "decimals": "-6",
        "precision": None,
        "scale": None,
        "language": None,
        "value": "123000000",
        "is_nil": False,
    }
    assert profit.ordinal == 2
    assert profit.value is None
    assert profit.is_nil is True


def test_extract_rejects_inventory_from_another_archive() -> None:
    """Never use caller-supplied member metadata for different raw bytes."""
    response = _zip_response()
    other = _zip_response(XBRL.replace(b"123000000", b"456000000"))

    with pytest.raises(EdinetXbrlFactParseError, match="^edinet_xbrl_facts_invalid$"):
        EdinetXbrlFactExtractor().extract(response, _inventory(other))


@pytest.mark.parametrize(
    "xbrl",
    [
        XBRL.replace(b'contextRef="CurrentYear"', b'contextRef="Missing"', 1),
        XBRL.replace(b'unitRef="JPY"', b'unitRef="Missing"', 1),
        XBRL.replace(
            b'<xbrli:context id="CurrentYear">',
            b'<xbrli:context id="CurrentYear"><xbrli:context id="CurrentYear" />',
        ),
        XBRL.replace(b">123000000</jp:Revenue>", b"></jp:Revenue>"),
        b"<not-closed>",
        b'<root xmlns:jp="https://example.invalid"><jp:Value contextRef="ctx">1</jp:Value></root>',
    ],
)
def test_extract_rejects_invalid_references_duplicates_empty_and_malformed_xml(xbrl: bytes) -> None:
    """Fail closed on source-native structures that cannot preserve fact meaning."""
    response = _zip_response(xbrl)
    with pytest.raises(EdinetXbrlFactParseError, match="^edinet_xbrl_facts_invalid$"):
        EdinetXbrlFactExtractor().extract(response, _inventory(response))


@pytest.mark.parametrize(
    "declaration",
    [b'<!DOCTYPE xbrli:xbrl SYSTEM "https://example.invalid/external.dtd">', b'<!ENTITY secret "unsafe">'],
)
def test_extract_rejects_doctype_and_entity_declarations(declaration: bytes) -> None:
    """Reject XML declaration mechanisms outside the bounded archive."""
    xbrl = XBRL.replace(b"<xbrli:xbrl", declaration + b"\n<xbrli:xbrl", 1)
    response = _zip_response(xbrl)

    with pytest.raises(EdinetXbrlFactParseError, match="^edinet_xbrl_facts_invalid$"):
        EdinetXbrlFactExtractor().extract(response, _inventory(response))


def test_extract_keeps_duplicate_fact_candidates_in_source_order() -> None:
    """Defer duplicate fact reconciliation while preserving deterministic order."""
    duplicate = b'<jp:Revenue contextRef="CurrentYear" unitRef="JPY" decimals="-6">999</jp:Revenue>'
    xbrl = XBRL.replace(b"</xbrli:xbrl>", duplicate + b"\n</xbrli:xbrl>")
    response = _zip_response(xbrl)

    extracted = EdinetXbrlFactExtractor().extract(response, _inventory(response))

    assert tuple(fact.ordinal for fact in extracted.facts) == (1, 2, 3)
    assert extracted.facts[0].concept_local_name == extracted.facts[2].concept_local_name
    assert extracted.facts[0].value != extracted.facts[2].value


def test_extract_preserves_fact_language() -> None:
    """Retain language identity for non-numeric source-native facts."""
    text_fact = b'<jp:Summary contextRef="CurrentYear" xml:lang="ja">synthetic summary</jp:Summary>'
    xbrl = XBRL.replace(b"</xbrli:xbrl>", text_fact + b"\n</xbrli:xbrl>")
    response = _zip_response(xbrl)

    extracted = EdinetXbrlFactExtractor().extract(response, _inventory(response))

    assert extracted.facts[-1].language == "ja"
    assert extracted.facts[-1].value == "synthetic summary"
