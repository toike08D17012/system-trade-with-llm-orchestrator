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
             xmlns:xbrldi="http://xbrl.org/2006/xbrldi"
             xmlns:iso4217="http://www.xbrl.org/2003/iso4217"
             xmlns:jp="https://example.invalid/synthetic">
  <xbrli:context id="CurrentYear">
    <xbrli:entity>
      <xbrli:identifier scheme="https://example.invalid/entity">SYNTHETIC-ENTITY</xbrli:identifier>
      <xbrli:segment>
        <xbrldi:explicitMember dimension="jp:ConsolidationAxis">jp:ConsolidatedMember</xbrldi:explicitMember>
      </xbrli:segment>
    </xbrli:entity>
    <xbrli:period>
      <xbrli:startDate>2025-04-01</xbrli:startDate><xbrli:endDate>2026-03-31</xbrli:endDate>
    </xbrli:period>
  </xbrli:context>
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
        media_type="application/octet-stream",
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
    context = extracted.contexts[0]
    assert context.entity_identifier == "SYNTHETIC-ENTITY"
    assert (context.period_kind, context.start_date, context.end_date) == (
        "duration",
        "2025-04-01",
        "2026-03-31",
    )
    assert context.dimensions[0].dimension.local_name == "ConsolidationAxis"
    assert context.dimensions[0].member.local_name == "ConsolidatedMember"
    unit = extracted.units[0]
    assert unit.numerator_measures[0].namespace == "http://www.xbrl.org/2003/iso4217"
    assert unit.numerator_measures[0].local_name == "JPY"
    assert unit.denominator_measures == ()
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
        XBRL.replace(b"2026-03-31</xbrli:endDate>", b"2025-03-31</xbrli:endDate>"),
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


def test_extract_preserves_instant_typed_dimension_and_divide_unit() -> None:
    """Retain instant periods, simple typed members, and divided units."""
    context = b"""<xbrli:context id="InstantContext">
      <xbrli:entity><xbrli:identifier scheme="https://example.invalid/entity">ENTITY</xbrli:identifier></xbrli:entity>
      <xbrli:period><xbrli:instant>2026-03-31</xbrli:instant></xbrli:period>
      <xbrli:scenario>
        <xbrldi:typedMember dimension="jp:RegionAxis"><jp:RegionValue>JP</jp:RegionValue></xbrldi:typedMember>
      </xbrli:scenario>
    </xbrli:context>"""
    unit = b"""<xbrli:unit id="JPYPerShare"><xbrli:divide>
      <xbrli:unitNumerator><xbrli:measure>iso4217:JPY</xbrli:measure></xbrli:unitNumerator>
      <xbrli:unitDenominator><xbrli:measure>xbrli:shares</xbrli:measure></xbrli:unitDenominator>
    </xbrli:divide></xbrli:unit>"""
    fact = b'<jp:Assets contextRef="InstantContext" unitRef="JPYPerShare" decimals="0">42</jp:Assets>'
    response = _zip_response(XBRL.replace(b"</xbrli:xbrl>", context + unit + fact + b"</xbrli:xbrl>"))

    extracted = EdinetXbrlFactExtractor().extract(response, _inventory(response))

    instant = extracted.contexts[1]
    assert (instant.period_kind, instant.instant) == ("instant", "2026-03-31")
    assert instant.dimensions[0].member_kind == "typed"
    assert instant.dimensions[0].member.local_name == "RegionValue"
    assert instant.dimensions[0].typed_value == "JP"
    divided = extracted.units[1]
    assert divided.numerator_measures[0].local_name == "JPY"
    assert divided.denominator_measures[0].local_name == "shares"
