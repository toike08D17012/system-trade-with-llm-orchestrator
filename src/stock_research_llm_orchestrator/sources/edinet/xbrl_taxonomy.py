"""Offline verification of explicitly approved, bounded taxonomy relationships."""

import io
import zipfile
from collections.abc import Mapping
from hashlib import sha256
from pathlib import PurePosixPath
from typing import Literal
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

from pydantic import Field

from stock_research_llm_orchestrator.contracts.base import Sha256Hex, StrictContractModel
from stock_research_llm_orchestrator.sources.edinet.document_retrieval import EdinetDocumentRetrievalAdapter
from stock_research_llm_orchestrator.sources.edinet.xbrl_facts import EdinetXbrlQName, _read_namespaces, _resolve_qname
from stock_research_llm_orchestrator.sources.protocol import BoundedSourceResponse


XLINK = "{http://www.w3.org/1999/xlink}"
XBRLDT = "{http://xbrl.org/2005/xbrldt}"
XBRLI = "{http://www.xbrl.org/2003/instance}"
XSD = "{http://www.w3.org/2001/XMLSchema}"
LINK = "{http://www.xbrl.org/2003/linkbase}"
MAX_MEMBER_BYTES = 8 * 1024 * 1024
MAX_SELECTED_BYTES = 32 * 1024 * 1024


class ConceptDeclaration(StrictContractModel):
    """An exact locator binding, with official external declarations pinned by policy."""

    href: str
    concept: EdinetXbrlQName
    period_type: Literal["instant", "duration"] | None = None
    monetary: bool = False


class RequiredArc(StrictContractModel):
    """A policy-reviewed relationship, never a guessed link from a concept name."""

    member: str
    role: str
    arcrole: str
    source: str
    target: str
    weight: str | None = None
    closed: str | None = None
    context_element: str | None = None


class TaxonomyRule(StrictContractModel):
    """Required declarations and complete reviewed scope paths for one metric."""

    concept_href: str
    arcs: tuple[RequiredArc, ...] = Field(min_length=1)


class TaxonomySpec(StrictContractModel):
    """Immutable member hashes and a small, approved declaration catalog."""

    schema_member: str
    member_hashes: dict[str, Sha256Hex]
    declarations: tuple[ConceptDeclaration, ...]


class TaxonomyProof(StrictContractModel):
    """Value-free proof or sanitized failure reasons for one metric."""

    verified: bool
    reasons: tuple[str, ...]
    concept: EdinetXbrlQName | None
    period_type: Literal["instant", "duration"] | None
    member_hashes: dict[str, Sha256Hex]
    arcs: tuple[RequiredArc, ...]


def _xml(body: bytes) -> ET.Element:
    # The approved provider members are UTF-8; reject declarations before parsing.
    try:
        text = body.decode("utf-8-sig")
    except UnicodeError:
        raise ValueError("unsupported_taxonomy_encoding") from None
    if "\x00" in text or "<!DOCTYPE" in text or "<!ENTITY" in text:
        raise ValueError("unsafe_taxonomy_xml")
    try:
        return ET.fromstring(text)
    except ET.ParseError:
        raise ValueError("invalid_taxonomy_xml") from None


def read_taxonomy_members(response: BoundedSourceResponse, spec: TaxonomySpec) -> dict[str, bytes]:
    """Validate ZIP paths and bound selected XML reads without extracting or fetching."""
    if sha256(response.body).hexdigest() != response.sha256:
        raise ValueError("taxonomy_archive_hash_mismatch")
    inventory = EdinetDocumentRetrievalAdapter().parse(response)
    sizes = {member.path: member.uncompressed_bytes for member in inventory.members}
    if set(spec.member_hashes) - sizes.keys():
        raise ValueError("taxonomy_member_missing")
    if any(sizes[name] > MAX_MEMBER_BYTES for name in spec.member_hashes):
        raise ValueError("taxonomy_member_too_large")
    if sum(sizes[name] for name in spec.member_hashes) > MAX_SELECTED_BYTES:
        raise ValueError("taxonomy_members_too_large")
    result = {}
    with zipfile.ZipFile(io.BytesIO(response.body)) as archive:
        for name, digest in spec.member_hashes.items():
            with archive.open(name) as stream:
                body = stream.read(sizes[name] + 1)
            if len(body) != sizes[name] or sha256(body).hexdigest() != digest:
                raise ValueError("taxonomy_member_hash_mismatch")
            _xml(body)
            result[name] = body
    return result


def _declaration(declaration: ConceptDeclaration, spec: TaxonomySpec, members: Mapping[str, bytes]) -> None:
    location, separator, identifier = declaration.href.partition("#")
    if not separator or not identifier or "#" in identifier:
        raise ValueError("taxonomy_invalid_locator")
    body = members[spec.schema_member]
    root = _xml(body)
    if root.tag != XSD + "schema":
        raise ValueError("taxonomy_schema_required")
    parsed = urlsplit(location)
    if parsed.scheme:
        # External schemas are never fetched. Only exact approved declarations and imports are allowed.
        if parsed.scheme not in {"http", "https"} or not any(
            node.get("schemaLocation") == location and node.get("namespace") == declaration.concept.namespace
            for node in root.findall(XSD + "import")
        ):
            raise ValueError("taxonomy_external_binding_mismatch")
        return
    path = PurePosixPath(spec.schema_member).parent / location
    if ".." in path.parts or path.is_absolute() or path.as_posix() != spec.schema_member:
        raise ValueError("taxonomy_local_schema_mismatch")
    declarations = [node for node in root.findall(XSD + "element") if node.get("id") == identifier]
    if len(declarations) != 1:
        raise ValueError("taxonomy_declaration_missing_or_ambiguous")
    node = declarations[0]
    if (
        root.get("targetNamespace") != declaration.concept.namespace
        or node.get("name") != declaration.concept.local_name
    ):
        raise ValueError("taxonomy_local_binding_mismatch")
    if declaration.monetary:
        namespaces = _read_namespaces(body)
        if (
            _resolve_qname(node.get("type", ""), namespaces)
            != EdinetXbrlQName(namespace=XBRLI[1:-1], local_name="monetaryItemType")
            or _resolve_qname(node.get("substitutionGroup", ""), namespaces)
            != EdinetXbrlQName(namespace=XBRLI[1:-1], local_name="item")
            or node.get("abstract", "false") not in {"false", "0"}
            or node.get(XBRLI + "periodType") != declaration.period_type
        ):
            raise ValueError("taxonomy_numeric_declaration_mismatch")


def _verify_network(body: bytes, role: str, required: tuple[RequiredArc, ...], allowed: set[str]) -> None:
    root = _xml(body)
    if root.tag != LINK + "linkbase":
        raise ValueError("taxonomy_linkbase_required")
    networks = [link for link in root if link.get(XLINK + "role") == role]
    if not networks:
        raise ValueError("taxonomy_role_missing")
    observed: set[tuple[str, str, str, str | None, str | None, str | None]] = set()
    relevant = {endpoint for arc in required for endpoint in (arc.source, arc.target)}
    for link in networks:
        if link.get(XLINK + "type") != "extended":
            raise ValueError("taxonomy_extended_link_required")
        locators: dict[str, str] = {}
        for node in link:
            if node.get(XLINK + "type") == "locator":
                label, href = node.get(XLINK + "label", ""), node.get(XLINK + "href", "")
                if label in locators:
                    raise ValueError("taxonomy_ambiguous_locator")
                locators[label] = href
        for node in link:
            if node.get(XLINK + "type") != "arc":
                continue
            source = locators.get(node.get(XLINK + "from", ""))
            target = locators.get(node.get(XLINK + "to", ""))
            if source not in relevant and target not in relevant:
                continue
            if source is None or target is None:
                raise ValueError("taxonomy_unresolved_locator")
            if (
                node.get("use", "optional") != "optional"
                or node.get("priority", "0") != "0"
                or node.get(XBRLDT + "targetRole") is not None
                or node.get(XBRLDT + "usable", "true") not in {"true", "1"}
            ):
                raise ValueError("taxonomy_unsupported_arc")
            arcrole = node.get(XLINK + "arcrole", "")
            kind = (
                "presentation"
                if arcrole.endswith("/parent-child")
                else "calculation"
                if arcrole.endswith("/summation-item")
                else "definition"
            )
            if link.tag != LINK + kind + "Link" or node.tag != LINK + kind + "Arc":
                raise ValueError("taxonomy_link_type_mismatch")
            if arcrole.endswith("/notAll"):
                raise ValueError("taxonomy_scope_exclusion")
            # A default on an approved axis must not also point to a different member.
            for arc in required:
                if (
                    arcrole.endswith("/dimension-default")
                    and arc.arcrole == arcrole
                    and source == arc.source
                    and target != arc.target
                ):
                    raise ValueError("taxonomy_default_conflict")
            observed.add(
                (
                    arcrole,
                    source,
                    target,
                    node.get("weight"),
                    node.get(XBRLDT + "closed"),
                    node.get(XBRLDT + "contextElement"),
                )
            )
    for arc in required:
        if arc.source not in allowed or arc.target not in allowed:
            raise ValueError("taxonomy_unapproved_declaration")
        if (arc.arcrole, arc.source, arc.target, arc.weight, arc.closed, arc.context_element) not in observed:
            raise ValueError("taxonomy_required_arc_missing")


def verify_taxonomy(members: Mapping[str, bytes], spec: TaxonomySpec, rule: TaxonomyRule) -> TaxonomyProof:
    """Verify only a metric's approved subgraph; unrelated taxonomy additions are ignored."""
    concept = None
    period_type = None
    used = {spec.schema_member, *(arc.member for arc in rule.arcs)}
    hashes = {name: sha256(members[name]).hexdigest() for name in sorted(used) if name in members}
    try:
        if any(hashes.get(name) != spec.member_hashes.get(name) or name not in members for name in used):
            raise ValueError("taxonomy_member_hash_mismatch")
        declarations = {d.href: d for d in spec.declarations}
        if len(declarations) != len(spec.declarations):
            raise ValueError("taxonomy_ambiguous_declarations")
        selected = declarations[rule.concept_href]
        if not selected.monetary or selected.period_type is None:
            raise ValueError("taxonomy_nonmonetary_concept")
        concept, period_type = selected.concept, selected.period_type
        endpoints = {rule.concept_href, *(end for arc in rule.arcs for end in (arc.source, arc.target))}
        for href in sorted(endpoints):
            _declaration(declarations[href], spec, members)
        for member, role in sorted({(arc.member, arc.role) for arc in rule.arcs}):
            required = tuple(arc for arc in rule.arcs if arc.member == member and arc.role == role)
            _verify_network(members[member], role, required, set(declarations))
    except ValueError, KeyError, ET.ParseError:
        # Raw external strings and XML parser errors must not escape diagnostics.
        return TaxonomyProof(
            verified=False,
            reasons=("taxonomy_proof_unresolved",),
            concept=concept,
            period_type=period_type,
            member_hashes=hashes,
            arcs=(),
        )
    return TaxonomyProof(
        verified=True, reasons=(), concept=concept, period_type=period_type, member_hashes=hashes, arcs=rule.arcs
    )
