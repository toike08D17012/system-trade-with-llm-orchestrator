"""Bounded extraction of source-native fact candidates from an EDINET archive."""

import io
import zipfile
from typing import Annotated
from xml.etree import ElementTree

from pydantic import Field, StringConstraints, model_validator

from stock_research_llm_orchestrator.contracts.base import Sha256Hex, StrictContractModel
from stock_research_llm_orchestrator.sources.edinet.document_retrieval import (
    EdinetDocumentArchive,
    EdinetDocumentRetrievalAdapter,
)
from stock_research_llm_orchestrator.sources.protocol import BoundedSourceResponse


_XBRLI_NAMESPACE = "http://www.xbrl.org/2003/instance"
_XSI_NIL = "{http://www.w3.org/2001/XMLSchema-instance}nil"
_XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
_MAX_XBRL_MEMBER_BYTES = 64 * 1024 * 1024
_MAX_FACTS = 250_000
_MAX_FACT_VALUE_LENGTH = 1_000_000
_NonEmptyBounded = Annotated[str, StringConstraints(min_length=1, max_length=1024)]
_OptionalBounded = Annotated[str, StringConstraints(min_length=1, max_length=128)] | None


class EdinetXbrlFactParseError(ValueError):
    """Sanitized failure raised for invalid or unsafe XBRL content."""


class EdinetXbrlFactCandidate(StrictContractModel):
    """One unnormalized fact candidate retaining its source XBRL semantics."""

    ordinal: int = Field(ge=1)
    source_member_path: _NonEmptyBounded
    concept_namespace: _NonEmptyBounded
    concept_local_name: _NonEmptyBounded
    context_ref: _NonEmptyBounded
    unit_ref: _OptionalBounded
    decimals: _OptionalBounded
    precision: _OptionalBounded
    scale: _OptionalBounded
    language: _OptionalBounded
    value: Annotated[str, StringConstraints(max_length=_MAX_FACT_VALUE_LENGTH)] | None
    is_nil: bool

    @model_validator(mode="after")
    def require_value_consistent_with_nil(self) -> EdinetXbrlFactCandidate:
        """Keep explicit XBRL nil separate from an empty or populated value."""
        if self.is_nil != (self.value is None):
            raise ValueError("edinet_xbrl_nil_value_mismatch")
        return self


class EdinetXbrlFactSet(StrictContractModel):
    """Source-native fact candidates tied to one exact EDINET archive."""

    archive_sha256: Sha256Hex
    xbrl_member_paths: tuple[_NonEmptyBounded, ...] = Field(min_length=1)
    facts: tuple[EdinetXbrlFactCandidate, ...] = Field(min_length=1)


class EdinetXbrlFactExtractor:
    """Read only inventoried XBRL members and produce unnormalized candidates."""

    def extract(self, response: BoundedSourceResponse, inventory: EdinetDocumentArchive) -> EdinetXbrlFactSet:
        """Verify inventory identity, bounded-read XBRL members, and parse facts."""
        try:
            verified_inventory = EdinetDocumentRetrievalAdapter().parse(response)
            if verified_inventory != inventory:
                raise ValueError("edinet_archive_inventory_mismatch")
            xbrl_members = tuple(member for member in inventory.members if member.is_xbrl)
            facts: list[EdinetXbrlFactCandidate] = []
            with zipfile.ZipFile(io.BytesIO(response.body), mode="r") as archive:
                for member in xbrl_members:
                    if member.uncompressed_bytes > _MAX_XBRL_MEMBER_BYTES:
                        raise ValueError("edinet_xbrl_member_size_exceeded")
                    with archive.open(member.path, mode="r") as source:
                        body = source.read(member.uncompressed_bytes + 1)
                    if len(body) != member.uncompressed_bytes:
                        raise ValueError("edinet_xbrl_member_size_mismatch")
                    member_facts = _parse_xbrl_member(member.path, body, first_ordinal=len(facts) + 1)
                    facts.extend(member_facts)
                    if len(facts) > _MAX_FACTS:
                        raise ValueError("edinet_xbrl_fact_limit_exceeded")
            return EdinetXbrlFactSet(
                archive_sha256=response.sha256,
                xbrl_member_paths=tuple(member.path for member in xbrl_members),
                facts=tuple(facts),
            )
        except (ElementTree.ParseError, KeyError, OSError, RuntimeError, ValueError, zipfile.BadZipFile) as exc:
            if isinstance(exc, EdinetXbrlFactParseError):
                raise
            raise EdinetXbrlFactParseError("edinet_xbrl_facts_invalid") from exc


def _parse_xbrl_member(path: str, body: bytes, *, first_ordinal: int) -> tuple[EdinetXbrlFactCandidate, ...]:
    if b"<!DOCTYPE" in body or b"<!ENTITY" in body:
        raise ValueError("unsafe_edinet_xbrl_declaration")
    root = ElementTree.fromstring(body)
    if root.tag != f"{{{_XBRLI_NAMESPACE}}}xbrl":
        raise ValueError("invalid_edinet_xbrl_root")
    contexts: set[str] = set()
    units: set[str] = set()
    raw_facts: list[ElementTree.Element] = []
    for element in root.iter():
        if element.tag == f"{{{_XBRLI_NAMESPACE}}}context":
            _add_unique_identifier(contexts, element.get("id"), "context")
        elif element.tag == f"{{{_XBRLI_NAMESPACE}}}unit":
            _add_unique_identifier(units, element.get("id"), "unit")
        elif element.get("contextRef") is not None:
            if len(raw_facts) >= _MAX_FACTS:
                raise ValueError("edinet_xbrl_fact_limit_exceeded")
            raw_facts.append(element)
    facts = tuple(
        _to_fact(path, element, contexts, units, ordinal=first_ordinal + offset)
        for offset, element in enumerate(raw_facts)
    )
    if first_ordinal + len(facts) - 1 > _MAX_FACTS:
        raise ValueError("edinet_xbrl_fact_limit_exceeded")
    return facts


def _add_unique_identifier(target: set[str], value: str | None, kind: str) -> None:
    if value is None or not value or value in target:
        raise ValueError(f"invalid_edinet_xbrl_{kind}_identifier")
    target.add(value)


def _to_fact(
    path: str,
    element: ElementTree.Element,
    contexts: set[str],
    units: set[str],
    *,
    ordinal: int,
) -> EdinetXbrlFactCandidate:
    namespace, local_name = _split_expanded_name(element.tag)
    context_ref = element.get("contextRef")
    if context_ref is None or context_ref not in contexts:
        raise ValueError("unknown_edinet_xbrl_context")
    unit_ref = element.get("unitRef")
    if unit_ref is not None and unit_ref not in units:
        raise ValueError("unknown_edinet_xbrl_unit")
    nil_value = element.get(_XSI_NIL, "false")
    if nil_value not in {"false", "0", "true", "1"}:
        raise ValueError("invalid_edinet_xbrl_nil")
    is_nil = nil_value in {"true", "1"}
    if len(element):
        raise ValueError("nested_edinet_xbrl_fact_unsupported")
    lexical_value = "".join(element.itertext()).strip()
    if is_nil:
        if lexical_value:
            raise ValueError("edinet_xbrl_nil_has_value")
        value = None
    else:
        if not lexical_value:
            raise ValueError("edinet_xbrl_fact_has_no_value")
        value = lexical_value
    return EdinetXbrlFactCandidate(
        ordinal=ordinal,
        source_member_path=path,
        concept_namespace=namespace,
        concept_local_name=local_name,
        context_ref=context_ref,
        unit_ref=unit_ref,
        decimals=element.get("decimals"),
        precision=element.get("precision"),
        scale=element.get("scale"),
        language=element.get(_XML_LANG),
        value=value,
        is_nil=is_nil,
    )


def _split_expanded_name(value: str) -> tuple[str, str]:
    if not value.startswith("{") or "}" not in value:
        raise ValueError("invalid_edinet_xbrl_concept_name")
    namespace, local_name = value[1:].split("}", maxsplit=1)
    if not namespace or not local_name:
        raise ValueError("invalid_edinet_xbrl_concept_name")
    return namespace, local_name
