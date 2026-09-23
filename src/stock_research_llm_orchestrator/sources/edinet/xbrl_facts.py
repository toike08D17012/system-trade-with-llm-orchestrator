"""Bounded extraction of source-native fact candidates from an EDINET archive."""

import io
import zipfile
from dataclasses import dataclass
from datetime import date
from typing import Annotated, Literal
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
_XBRLDI_NAMESPACE = "http://xbrl.org/2006/xbrldi"
_MAX_XBRL_MEMBER_BYTES = 64 * 1024 * 1024
_MAX_FACTS = 250_000
_MAX_FACT_VALUE_LENGTH = 1_000_000
_NonEmptyBounded = Annotated[str, StringConstraints(min_length=1, max_length=1024)]
_OptionalBounded = Annotated[str, StringConstraints(min_length=1, max_length=128)] | None
_DimensionLocation = Literal["segment", "scenario"]
_PeriodKind = Literal["instant", "duration", "forever"]


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


class EdinetXbrlQName(StrictContractModel):
    """One QName resolved against declarations in the source XBRL member."""

    namespace: _NonEmptyBounded
    local_name: _NonEmptyBounded


class EdinetXbrlDimension(StrictContractModel):
    """One explicit or simple typed dimension attached to an XBRL context."""

    location: _DimensionLocation
    dimension: EdinetXbrlQName
    member_kind: Literal["explicit", "typed"]
    member: EdinetXbrlQName
    typed_value: Annotated[str, StringConstraints(min_length=1, max_length=_MAX_FACT_VALUE_LENGTH)] | None

    @model_validator(mode="after")
    def require_typed_value_only_for_typed_member(self) -> EdinetXbrlDimension:
        """Keep explicit QName members distinct from typed lexical values."""
        if (self.member_kind == "typed") != (self.typed_value is not None):
            raise ValueError("edinet_xbrl_dimension_value_mismatch")
        return self


class EdinetXbrlContext(StrictContractModel):
    """Entity, period, and dimensional identity for one XBRL context."""

    source_member_path: _NonEmptyBounded
    context_id: _NonEmptyBounded
    entity_identifier_scheme: _NonEmptyBounded
    entity_identifier: _NonEmptyBounded
    period_kind: _PeriodKind
    instant: str | None
    start_date: str | None
    end_date: str | None
    dimensions: tuple[EdinetXbrlDimension, ...]

    @model_validator(mode="after")
    def require_period_shape(self) -> EdinetXbrlContext:
        """Require exactly the date fields belonging to the selected period kind."""
        if self.period_kind == "instant":
            valid = self.instant is not None and self.start_date is None and self.end_date is None
        elif self.period_kind == "duration":
            valid = self.instant is None and self.start_date is not None and self.end_date is not None
        else:
            valid = self.instant is None and self.start_date is None and self.end_date is None
        if not valid:
            raise ValueError("edinet_xbrl_context_period_mismatch")
        parsed_dates = tuple(
            date.fromisoformat(value) if value is not None else None
            for value in (
                self.instant,
                self.start_date,
                self.end_date,
            )
        )
        if self.period_kind == "duration":
            start, end = parsed_dates[1], parsed_dates[2]
            if start is None or end is None or start > end:
                raise ValueError("edinet_xbrl_context_period_reversed")
        keys = tuple((dimension.location, dimension.dimension) for dimension in self.dimensions)
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate_edinet_xbrl_dimension")
        return self


class EdinetXbrlUnit(StrictContractModel):
    """Simple or divided unit definition referenced by numeric facts."""

    source_member_path: _NonEmptyBounded
    unit_id: _NonEmptyBounded
    numerator_measures: tuple[EdinetXbrlQName, ...] = Field(min_length=1)
    denominator_measures: tuple[EdinetXbrlQName, ...]


@dataclass(frozen=True)
class _ParsedXbrlMember:
    contexts: tuple[EdinetXbrlContext, ...]
    units: tuple[EdinetXbrlUnit, ...]
    facts: tuple[EdinetXbrlFactCandidate, ...]


class EdinetXbrlFactSet(StrictContractModel):
    """Source-native fact candidates tied to one exact EDINET archive."""

    archive_sha256: Sha256Hex
    xbrl_member_paths: tuple[_NonEmptyBounded, ...] = Field(min_length=1)
    contexts: tuple[EdinetXbrlContext, ...] = Field(min_length=1)
    units: tuple[EdinetXbrlUnit, ...]
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
            contexts: list[EdinetXbrlContext] = []
            units: list[EdinetXbrlUnit] = []
            facts: list[EdinetXbrlFactCandidate] = []
            with zipfile.ZipFile(io.BytesIO(response.body), mode="r") as archive:
                for member in xbrl_members:
                    if member.uncompressed_bytes > _MAX_XBRL_MEMBER_BYTES:
                        raise ValueError("edinet_xbrl_member_size_exceeded")
                    with archive.open(member.path, mode="r") as source:
                        body = source.read(member.uncompressed_bytes + 1)
                    if len(body) != member.uncompressed_bytes:
                        raise ValueError("edinet_xbrl_member_size_mismatch")
                    parsed = _parse_xbrl_member(member.path, body, first_ordinal=len(facts) + 1)
                    contexts.extend(parsed.contexts)
                    units.extend(parsed.units)
                    facts.extend(parsed.facts)
                    if len(facts) > _MAX_FACTS:
                        raise ValueError("edinet_xbrl_fact_limit_exceeded")
            return EdinetXbrlFactSet(
                archive_sha256=response.sha256,
                xbrl_member_paths=tuple(member.path for member in xbrl_members),
                contexts=tuple(contexts),
                units=tuple(units),
                facts=tuple(facts),
            )
        except (ElementTree.ParseError, KeyError, OSError, RuntimeError, ValueError, zipfile.BadZipFile) as exc:
            if isinstance(exc, EdinetXbrlFactParseError):
                raise
            raise EdinetXbrlFactParseError("edinet_xbrl_facts_invalid") from exc


def _parse_xbrl_member(path: str, body: bytes, *, first_ordinal: int) -> _ParsedXbrlMember:
    if b"<!DOCTYPE" in body or b"<!ENTITY" in body:
        raise ValueError("unsafe_edinet_xbrl_declaration")
    namespaces = _read_namespaces(body)
    root = ElementTree.fromstring(body)
    if root.tag != f"{{{_XBRLI_NAMESPACE}}}xbrl":
        raise ValueError("invalid_edinet_xbrl_root")
    contexts: dict[str, EdinetXbrlContext] = {}
    units: dict[str, EdinetXbrlUnit] = {}
    raw_facts: list[ElementTree.Element] = []
    for element in root.iter():
        if element.tag == f"{{{_XBRLI_NAMESPACE}}}context":
            context = _to_context(path, element, namespaces)
            _add_unique_definition(contexts, context.context_id, context, "context")
        elif element.tag == f"{{{_XBRLI_NAMESPACE}}}unit":
            unit = _to_unit(path, element, namespaces)
            _add_unique_definition(units, unit.unit_id, unit, "unit")
        elif element.get("contextRef") is not None:
            if len(raw_facts) >= _MAX_FACTS:
                raise ValueError("edinet_xbrl_fact_limit_exceeded")
            raw_facts.append(element)
    facts = tuple(
        _to_fact(path, element, set(contexts), set(units), ordinal=first_ordinal + offset)
        for offset, element in enumerate(raw_facts)
    )
    if first_ordinal + len(facts) - 1 > _MAX_FACTS:
        raise ValueError("edinet_xbrl_fact_limit_exceeded")
    return _ParsedXbrlMember(tuple(contexts.values()), tuple(units.values()), facts)


def _add_unique_definition[T](target: dict[str, T], identifier: str, value: T, kind: str) -> None:
    if identifier in target:
        raise ValueError(f"invalid_edinet_xbrl_{kind}_identifier")
    target[identifier] = value


def _read_namespaces(body: bytes) -> dict[str, str]:
    namespaces: dict[str, str] = {}
    for _event, (prefix, namespace) in ElementTree.iterparse(io.BytesIO(body), events=("start-ns",)):
        if prefix in namespaces and namespaces[prefix] != namespace:
            raise ValueError("conflicting_edinet_xbrl_namespace_prefix")
        namespaces[prefix] = namespace
    return namespaces


def _to_context(path: str, element: ElementTree.Element, namespaces: dict[str, str]) -> EdinetXbrlContext:
    entity = _single_child(element, "entity")
    identifier = _single_child(entity, "identifier")
    period = _single_child(element, "period")
    period_kind, instant, start_date, end_date = _parse_period(period)
    dimensions: list[EdinetXbrlDimension] = []
    locations: tuple[_DimensionLocation, ...] = ("segment", "scenario")
    for location in locations:
        container = element.find(f".//{{{_XBRLI_NAMESPACE}}}{location}")
        if container is not None:
            dimensions.extend(_parse_dimensions(container, location, namespaces))
    return EdinetXbrlContext(
        source_member_path=path,
        context_id=_attribute(element, "id", "context"),
        entity_identifier_scheme=_attribute(identifier, "scheme", "entity_identifier"),
        entity_identifier=_text(identifier, "entity_identifier"),
        period_kind=period_kind,
        instant=instant,
        start_date=start_date,
        end_date=end_date,
        dimensions=tuple(dimensions),
    )


def _parse_period(period: ElementTree.Element) -> tuple[_PeriodKind, str | None, str | None, str | None]:
    instant = period.findall(f"{{{_XBRLI_NAMESPACE}}}instant")
    starts = period.findall(f"{{{_XBRLI_NAMESPACE}}}startDate")
    ends = period.findall(f"{{{_XBRLI_NAMESPACE}}}endDate")
    forever = period.findall(f"{{{_XBRLI_NAMESPACE}}}forever")
    if len(instant) == 1 and not starts and not ends and not forever:
        return "instant", _text(instant[0], "instant"), None, None
    if not instant and len(starts) == 1 and len(ends) == 1 and not forever:
        return "duration", None, _text(starts[0], "start_date"), _text(ends[0], "end_date")
    if not instant and not starts and not ends and len(forever) == 1:
        return "forever", None, None, None
    raise ValueError("invalid_edinet_xbrl_period")


def _parse_dimensions(
    container: ElementTree.Element, location: _DimensionLocation, namespaces: dict[str, str]
) -> tuple[EdinetXbrlDimension, ...]:
    dimensions: list[EdinetXbrlDimension] = []
    for element in container:
        dimension = _resolve_qname(_attribute(element, "dimension", "dimension"), namespaces)
        if element.tag == f"{{{_XBRLDI_NAMESPACE}}}explicitMember":
            dimensions.append(
                EdinetXbrlDimension(
                    location=location,
                    dimension=dimension,
                    member_kind="explicit",
                    member=_resolve_qname(_text(element, "explicit_member"), namespaces),
                    typed_value=None,
                )
            )
        elif element.tag == f"{{{_XBRLDI_NAMESPACE}}}typedMember":
            children = tuple(element)
            if len(children) != 1 or len(children[0]):
                raise ValueError("unsupported_edinet_xbrl_typed_member")
            namespace, local_name = _split_expanded_name(children[0].tag)
            dimensions.append(
                EdinetXbrlDimension(
                    location=location,
                    dimension=dimension,
                    member_kind="typed",
                    member=EdinetXbrlQName(namespace=namespace, local_name=local_name),
                    typed_value=_text(children[0], "typed_member"),
                )
            )
        else:
            raise ValueError("unsupported_edinet_xbrl_context_content")
    return tuple(dimensions)


def _to_unit(path: str, element: ElementTree.Element, namespaces: dict[str, str]) -> EdinetXbrlUnit:
    measures = element.findall(f"{{{_XBRLI_NAMESPACE}}}measure")
    divides = element.findall(f"{{{_XBRLI_NAMESPACE}}}divide")
    if measures and not divides and len(measures) == len(element):
        numerator = tuple(_resolve_qname(_text(measure, "unit_measure"), namespaces) for measure in measures)
        denominator: tuple[EdinetXbrlQName, ...] = ()
    elif not measures and len(divides) == 1 and len(element) == 1:
        numerator = _measure_group(divides[0], "unitNumerator", namespaces)
        denominator = _measure_group(divides[0], "unitDenominator", namespaces)
    else:
        raise ValueError("invalid_edinet_xbrl_unit")
    return EdinetXbrlUnit(
        source_member_path=path,
        unit_id=_attribute(element, "id", "unit"),
        numerator_measures=numerator,
        denominator_measures=denominator,
    )


def _measure_group(divide: ElementTree.Element, name: str, namespaces: dict[str, str]) -> tuple[EdinetXbrlQName, ...]:
    groups = divide.findall(f"{{{_XBRLI_NAMESPACE}}}{name}")
    if len(groups) != 1:
        raise ValueError("invalid_edinet_xbrl_divide_unit")
    measures = groups[0].findall(f"{{{_XBRLI_NAMESPACE}}}measure")
    if not measures or len(measures) != len(groups[0]):
        raise ValueError("invalid_edinet_xbrl_divide_unit")
    return tuple(_resolve_qname(_text(measure, "unit_measure"), namespaces) for measure in measures)


def _resolve_qname(value: str, namespaces: dict[str, str]) -> EdinetXbrlQName:
    if value.count(":") > 1:
        raise ValueError("invalid_edinet_xbrl_qname")
    prefix, local_name = value.split(":", 1) if ":" in value else ("", value)
    namespace = namespaces.get(prefix)
    if namespace is None or not local_name:
        raise ValueError("unknown_edinet_xbrl_qname_prefix")
    return EdinetXbrlQName(namespace=namespace, local_name=local_name)


def _single_child(element: ElementTree.Element, name: str) -> ElementTree.Element:
    children = element.findall(f"{{{_XBRLI_NAMESPACE}}}{name}")
    if len(children) != 1:
        raise ValueError(f"invalid_edinet_xbrl_{name}")
    return children[0]


def _attribute(element: ElementTree.Element, name: str, kind: str) -> str:
    value = element.get(name)
    if not value:
        raise ValueError(f"invalid_edinet_xbrl_{kind}")
    return value


def _text(element: ElementTree.Element, kind: str) -> str:
    value = "".join(element.itertext()).strip()
    if not value:
        raise ValueError(f"invalid_edinet_xbrl_{kind}")
    return value


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
