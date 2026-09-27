"""Bounded, network-free verification of policy-selected taxonomy relationships."""

import io
import zipfile
from hashlib import sha256

import pytest

from stock_research_llm_orchestrator.sources.edinet.xbrl_facts import EdinetXbrlQName
from stock_research_llm_orchestrator.sources.edinet.xbrl_taxonomy import (
    ConceptDeclaration,
    RequiredArc,
    TaxonomyRule,
    TaxonomySpec,
    read_taxonomy_members,
    verify_taxonomy,
)
from stock_research_llm_orchestrator.sources.protocol import BoundedSourceResponse


SCHEMA = b"""<xs:schema xmlns:xs="http://www.w3.org/2001/XMLSchema"
 xmlns:xbrli="http://www.xbrl.org/2003/instance" targetNamespace="urn:synthetic">
 <xs:element id="Revenue" name="Revenue" type="xbrli:monetaryItemType"
 substitutionGroup="xbrli:item" xbrli:periodType="duration"/>
 <xs:element id="Axis" name="Axis"/><xs:element id="Member" name="Member"/>
 </xs:schema>"""
LINKBASE = b"""<link:linkbase xmlns:link="http://www.xbrl.org/2003/linkbase"
 xmlns:xlink="http://www.w3.org/1999/xlink">
 <link:definitionLink xlink:type="extended" xlink:role="urn:consolidated">
 <link:loc xlink:type="locator" xlink:href="schema.xsd#Axis" xlink:label="axis"/>
 <link:loc xlink:type="locator" xlink:href="schema.xsd#Member" xlink:label="member"/>
 <link:definitionArc xlink:type="arc" xlink:arcrole="http://xbrl.org/int/dim/arcrole/dimension-default"
 xlink:from="axis" xlink:to="member"/>
 </link:definitionLink></link:linkbase>"""


def _case(schema: bytes = SCHEMA, links: bytes = LINKBASE) -> tuple[dict[str, bytes], TaxonomySpec, TaxonomyRule]:
    members = {"schema.xsd": schema, "definition.xml": links}
    declarations = tuple(
        ConceptDeclaration(
            href=f"schema.xsd#{name}",
            concept=EdinetXbrlQName(namespace="urn:synthetic", local_name=name),
            monetary=name == "Revenue",
            period_type="duration" if name == "Revenue" else None,
        )
        for name in ("Revenue", "Axis", "Member")
    )
    spec = TaxonomySpec(
        schema_member="schema.xsd",
        member_hashes={n: sha256(b).hexdigest() for n, b in members.items()},
        declarations=declarations,
    )
    rule = TaxonomyRule(
        concept_href="schema.xsd#Revenue",
        arcs=(
            RequiredArc(
                member="definition.xml",
                role="urn:consolidated",
                arcrole="http://xbrl.org/int/dim/arcrole/dimension-default",
                source="schema.xsd#Axis",
                target="schema.xsd#Member",
            ),
        ),
    )
    return members, spec, rule


def _response(members: dict[str, bytes]) -> BoundedSourceResponse:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, body in {**members, "facts.xbrl": b"<unused/>"}.items():
            archive.writestr(name, body)
    body = buffer.getvalue()
    return BoundedSourceResponse(
        physical_attempt_id="synthetic-attempt",
        body=body,
        sha256=sha256(body).hexdigest(),
        media_type="application/octet-stream",
        encoding="binary",
    )


def test_verified_local_schema_and_default() -> None:
    """Bind locator identity through the actual local schema declaration."""
    members, spec, rule = _case()
    loaded = read_taxonomy_members(_response(members), spec)
    proof = verify_taxonomy(loaded, spec, rule)
    assert proof.verified and proof.period_type == "duration" and proof.arcs == rule.arcs


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (b"urn:consolidated", b"urn:other"),
        (b'xlink:to="member"', b'xlink:to="missing"'),
        (b"schema.xsd#Member", b"schema.xsd#Unknown"),
        (b'xlink:from="axis"', b'use="prohibited" xlink:from="axis"'),
        (b'xlink:from="axis"', b'priority="1" xlink:from="axis"'),
        (b'xlink:from="axis"', b'xmlns:d="http://xbrl.org/2005/xbrldt" d:targetRole="urn:other" xlink:from="axis"'),
        (b"dimension-default", b"dimension-domain"),
    ],
)
def test_required_relationships_cannot_be_guessed(old: bytes, new: bytes) -> None:
    """Wrong roles, unresolved locators and unsupported overrides remain unaccepted."""
    members, spec, rule = _case(links=LINKBASE.replace(old, new))
    assert not verify_taxonomy(members, spec, rule).verified


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (b"urn:synthetic", b"urn:wrong"),
        (b'name="Revenue"', b'name="Other"'),
        (b'periodType="duration"', b'periodType="instant"'),
        (b'type="xbrli:monetaryItemType"', b'type="xbrli:stringItemType"'),
        (b'name="Revenue"', b'name="Revenue" abstract="true"'),
    ],
)
def test_declaration_must_match_qname_and_type(old: bytes, new: bytes) -> None:
    """A familiar fragment identifier does not prove concept identity or numeric type."""
    members, spec, rule = _case(schema=SCHEMA.replace(old, new))
    assert not verify_taxonomy(members, spec, rule).verified


def test_unrelated_extensions_are_ignored() -> None:
    """Unrelated roles and elements do not impose additional provider formatting rules."""
    links = LINKBASE.replace(b"</link:linkbase>", b'<link:definitionLink xlink:role="urn:new"/></link:linkbase>')
    members, spec, rule = _case(
        schema=SCHEMA.replace(b"</xs:schema>", b'<xs:element name="New"/></xs:schema>'), links=links
    )
    assert verify_taxonomy(members, spec, rule).verified


def test_archive_and_member_tampering() -> None:
    """The inventory and every selected member remain bound to exact bytes."""
    members, spec, rule = _case()
    with pytest.raises(ValueError):
        read_taxonomy_members(_response({**members, "../escape.xml": b"bad"}), spec)
    with pytest.raises(ValueError):
        read_taxonomy_members(_response({**members, "definition.xml": b"bad"}), spec)
    assert not verify_taxonomy({**members, "definition.xml": b"bad"}, spec, rule).verified
    with pytest.raises(ValueError):
        read_taxonomy_members(_response(members).model_copy(update={"sha256": "0" * 64}), spec)


@pytest.mark.parametrize(
    "schema",
    [
        b'<!DOCTYPE schema [<!ENTITY secret "hidden">]>' + SCHEMA,
        SCHEMA.decode().encode("utf-16"),
    ],
)
def test_unsafe_xml_is_rejected(schema: bytes) -> None:
    """Do not resolve entities or bypass the declaration check using another encoding."""
    members, spec, _rule = _case(schema=schema)
    with pytest.raises(ValueError):
        read_taxonomy_members(_response(members), spec)


def test_selected_member_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bound decompression before reading selected XML."""
    members, spec, _rule = _case()
    monkeypatch.setattr("stock_research_llm_orchestrator.sources.edinet.xbrl_taxonomy.MAX_MEMBER_BYTES", 10)
    with pytest.raises(ValueError, match="too_large"):
        read_taxonomy_members(_response(members), spec)


def test_external_declaration_requires_matching_import() -> None:
    """Approved external QName declarations cannot silently change schema or namespace."""
    members, spec, rule = _case()
    href = "https://example.invalid/standard.xsd#Revenue"
    declaration = spec.declarations[0].model_copy(update={"href": href})
    spec = spec.model_copy(update={"declarations": (declaration, *spec.declarations[1:])})
    rule = rule.model_copy(update={"concept_href": href})
    assert not verify_taxonomy(members, spec, rule).verified
    schema = SCHEMA.replace(
        b"</xs:schema>",
        b'<xs:import namespace="urn:synthetic" schemaLocation="https://example.invalid/standard.xsd"/></xs:schema>',
    )
    members["schema.xsd"] = schema
    spec = spec.model_copy(update={"member_hashes": {n: sha256(b).hexdigest() for n, b in members.items()}})
    assert verify_taxonomy(members, spec, rule).verified


def test_conflicting_default_is_unresolved() -> None:
    """A second member on the same default axis cannot be silently ignored."""
    extension = b"""<link:loc xlink:type="locator" xlink:href="schema.xsd#Other" xlink:label="other"/>
    <link:definitionArc xlink:type="arc" xlink:arcrole="http://xbrl.org/int/dim/arcrole/dimension-default"
    xlink:from="axis" xlink:to="other"/>"""
    members, spec, rule = _case(
        links=LINKBASE.replace(b"</link:definitionLink>", extension + b"</link:definitionLink>")
    )
    assert not verify_taxonomy(members, spec, rule).verified


def test_unknown_element_cannot_supply_required_arc() -> None:
    """Only XBRL linkbase arcs can establish a taxonomy relationship."""
    members, spec, rule = _case(links=LINKBASE.replace(b"link:definitionArc", b"link:Unknown"))
    assert not verify_taxonomy(members, spec, rule).verified
