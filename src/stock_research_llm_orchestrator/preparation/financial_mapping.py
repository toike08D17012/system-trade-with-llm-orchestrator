"""Versioned, value-free review of exact XBRL mapping proposals."""

import json
import re
from collections.abc import Mapping
from datetime import date
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from stock_research_llm_orchestrator.contracts.base import Sha256Hex, StrictContractModel
from stock_research_llm_orchestrator.preparation.financial_disclosure import (
    FinancialInput,
    FinancialManifest,
    validate_financial,
)
from stock_research_llm_orchestrator.preparation.fx_evidence import read_bundle
from stock_research_llm_orchestrator.preparation.market_revalidation import _safe_path
from stock_research_llm_orchestrator.preparation.price_fx_run import publish_run_preparation
from stock_research_llm_orchestrator.sources.edinet.xbrl_document import EdinetXbrlDocument
from stock_research_llm_orchestrator.sources.edinet.xbrl_facts import (
    EdinetXbrlContext,
    EdinetXbrlFactCandidate,
    EdinetXbrlFactSet,
    EdinetXbrlQName,
)


EDINET_SCHEME = "http://disclosure.edinet-fsa.go.jp"
Metric = Literal["revenue", "operating_profit", "parent_profit", "assets", "equity", "operating_cash_flow"]
METRICS: tuple[Metric, ...] = (
    "revenue",
    "operating_profit",
    "parent_profit",
    "assets",
    "equity",
    "operating_cash_flow",
)


class MappingRule(StrictContractModel):
    """Exact discovery rule awaiting semantic and scope approval."""

    metric: Metric
    concepts: tuple[EdinetXbrlQName, ...] = Field(min_length=1)
    evidence_references: tuple[str, ...] = Field(min_length=1)


class MappingProposal(StrictContractModel):
    """An explicit period and six draft rules, never an acceptance policy."""

    version: Literal[1] = 1
    status: Literal["draft"] = "draft"
    start_date: date
    end_date: date
    rules: tuple[MappingRule, ...]

    @model_validator(mode="after")
    def check_scope(self) -> MappingProposal:
        """Reject ambiguous proposal coverage and reversed periods."""
        if self.start_date > self.end_date or sorted(r.metric for r in self.rules) != sorted(METRICS):
            raise ValueError("invalid_mapping_scope")
        concepts = [concept for rule in self.rules for concept in rule.concepts]
        if len(concepts) != len(set(concepts)):
            raise ValueError("ambiguous_mapping_concept")
        return self


class EntityMatch(StrictContractModel):
    """Preserve source identity and distinguish unsupported instance suffixes."""

    rule_version: Literal[1] = 1
    scheme: str
    identifier: str
    edinet_code: str | None
    suffix: str | None
    reason: str


def match_entity(context: EdinetXbrlContext, expected_code: str | None) -> EntityMatch:
    """Match the official scheme and a regular issuer's initial instance only."""
    matched = re.fullmatch(r"(E[0-9]{5})-([0-9]{3})", context.entity_identifier)
    code, suffix = matched.groups() if matched else (None, None)
    if context.entity_identifier_scheme != EDINET_SCHEME:
        reason = "unsupported_entity_scheme"
    elif matched is None:
        reason = "unsupported_entity_identifier"
    elif expected_code is None:
        reason = "issuer_binding_unresolved"
    elif code != expected_code:
        reason = "different_entity"
    elif suffix != "000":
        reason = "unsupported_instance_suffix"
    else:
        reason = "matched"
    return EntityMatch(
        scheme=context.entity_identifier_scheme,
        identifier=context.entity_identifier,
        edinet_code=code,
        suffix=suffix,
        reason=reason,
    )


class CandidateReview(StrictContractModel):
    """Allowlisted fact metadata; lexical values and text blocks are never copied."""

    ordinal: int
    source_member_path: str
    concept: EdinetXbrlQName
    context_ref: str
    unit_ref: str | None
    decimals: str | None
    is_nil: bool
    period_kind: str
    start_date: str | None
    end_date: str | None
    instant: str | None
    dimension_axes: tuple[EdinetXbrlQName, ...]
    dimension_members: tuple[EdinetXbrlQName, ...]
    unit_numerator: tuple[EdinetXbrlQName, ...]
    unit_denominator: tuple[EdinetXbrlQName, ...]
    precision: str | None
    scale: str | None
    entity: EntityMatch
    reasons: tuple[str, ...]


class MetricReview(StrictContractModel):
    """Compare only numerically comparable candidates, without exporting values."""

    metric: Metric
    status: Literal["missing", "excluded", "nil", "candidate", "duplicate", "conflicting"]
    candidates: tuple[CandidateReview, ...]
    comparable_ordinals: tuple[int, ...]
    review_reasons: tuple[str, ...] = ("mapping_not_approved", "consolidation_scope_unconfirmed")


class ArchiveReview(StrictContractModel):
    """Keep filing exclusions and per-context identities separate."""

    key: str
    archive_sha256: Sha256Hex
    entities: tuple[EntityMatch, ...]
    mixed_entities: bool
    filing_reasons: tuple[str, ...]
    metrics: tuple[MetricReview, ...]


class MappingReview(StrictContractModel):
    """Sidecar review tied to immutable legacy evidence and a draft proposal."""

    version: Literal[1] = 1
    kind: Literal["financial-mapping-review"] = "financial-mapping-review"
    status: Literal["pending"] = "pending"
    analysis_ready: Literal[False] = False
    source_manifest_sha256: Sha256Hex
    proposal_sha256: Sha256Hex
    inherited_reasons: tuple[str, ...]
    archives: tuple[ArchiveReview, ...]


def _number(fact: EdinetXbrlFactCandidate) -> Decimal | None:
    if fact.is_nil:
        return None
    # XBRL monetary values use decimal lexical syntax, not exponent notation.
    if fact.value is None or re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)", fact.value) is None:
        return None
    try:
        result = Decimal(fact.value)
    except InvalidOperation:
        return None
    return result if result.is_finite() else None


def review_metric(
    facts: EdinetXbrlFactSet,
    rule: MappingRule,
    proposal: MappingProposal,
    expected_code: str | None,
    filing_reasons: tuple[str, ...] = (),
) -> MetricReview:
    """Filter exact QNames by entity, period, dimensions and simple JPY units."""
    contexts = {(c.source_member_path, c.context_id): c for c in facts.contexts}
    units = {(u.source_member_path, u.unit_id): u for u in facts.units}
    candidates: list[CandidateReview] = []
    comparable: list[int] = []
    numbers: list[Decimal] = []
    nil_count = 0
    for fact in facts.facts:
        concept = EdinetXbrlQName(namespace=fact.concept_namespace, local_name=fact.concept_local_name)
        if concept not in rule.concepts:
            continue
        context = contexts[(fact.source_member_path, fact.context_ref)]
        entity = match_entity(context, expected_code)
        reasons = set(filing_reasons)
        if entity.reason != "matched":
            reasons.add(entity.reason)
        instant = rule.metric in {"assets", "equity"}
        if instant:
            period_matches = context.period_kind == "instant" and context.instant == proposal.end_date.isoformat()
        else:
            period_matches = (
                context.period_kind == "duration"
                and context.start_date == proposal.start_date.isoformat()
                and context.end_date == proposal.end_date.isoformat()
            )
        if not period_matches:
            reasons.add("period_mismatch")
        if context.dimensions:
            reasons.add("dimensions_outside_proposal")
        unit = units.get((fact.source_member_path, fact.unit_ref or ""))
        if (
            unit is None
            or unit.denominator_measures
            or unit.numerator_measures
            != (EdinetXbrlQName(namespace="http://www.xbrl.org/2003/iso4217", local_name="JPY"),)
        ):
            reasons.add("unsupported_unit")
        if fact.scale is not None:
            reasons.add("unsupported_scale")
        number = _number(fact)
        if not fact.is_nil and number is None:
            reasons.add("invalid_numeric_value")
        if not reasons:
            comparable.append(fact.ordinal)
            if fact.is_nil:
                nil_count += 1
            elif number is not None:
                numbers.append(number)
        if fact.is_nil:
            reasons.add("nil")
        candidates.append(
            CandidateReview(
                ordinal=fact.ordinal,
                source_member_path=fact.source_member_path,
                concept=concept,
                context_ref=fact.context_ref,
                unit_ref=fact.unit_ref,
                decimals=fact.decimals,
                is_nil=fact.is_nil,
                period_kind=context.period_kind,
                start_date=context.start_date,
                end_date=context.end_date,
                instant=context.instant,
                dimension_axes=tuple(d.dimension for d in context.dimensions),
                dimension_members=tuple(d.member for d in context.dimensions),
                unit_numerator=unit.numerator_measures if unit else (),
                unit_denominator=unit.denominator_measures if unit else (),
                precision=fact.precision,
                scale=fact.scale,
                entity=entity,
                reasons=tuple(sorted(reasons)),
            )
        )
    status: Literal["missing", "excluded", "nil", "candidate", "duplicate", "conflicting"]
    if not candidates:
        status = "missing"
    elif not comparable:
        status = "excluded"
    elif (nil_count and numbers) or len(set(numbers)) > 1:
        status = "conflicting"
    elif nil_count:
        status = "nil"
    elif len(numbers) > 1:
        status = "duplicate"
    else:
        status = "candidate"
    return MetricReview(
        metric=rule.metric, status=status, candidates=tuple(candidates), comparable_ordinals=tuple(comparable)
    )


def evaluate_mapping(source: Mapping[str, bytes], proposal_bytes: bytes) -> MappingReview:
    """Revalidate source bytes before producing a metadata-only sidecar."""
    validate_financial(source)
    proposal = MappingProposal.model_validate_json(proposal_bytes)
    inputs = FinancialInput.model_validate_json(source["inputs.json"])
    manifest = FinancialManifest.model_validate_json(source["manifest.json"])
    expected = (
        inputs.issuer.edinet_code if inputs.issuer and "issuer_binding_unresolved" not in manifest.reasons else None
    )
    documents = json.loads(source["candidates.json"])
    archives: list[ArchiveReview] = []
    for key, value in sorted(documents.items()):
        document = EdinetXbrlDocument.model_validate_json(json.dumps(value))
        observations = [filing for filing in manifest.filings if key in filing.archive_keys]
        reasons = {reason for filing in observations for reason in filing.reasons if reason != "xbrl_entity_unresolved"}
        if not observations:
            reasons.add("archive_without_filing_list")
        if any(f.document.document_type.value != "120" for f in observations):
            reasons.add("unsupported_filing_type")
        if any(
            (f.document.period_start, f.document.period_end)
            != (proposal.start_date.isoformat(), proposal.end_date.isoformat())
            for f in observations
        ):
            reasons.add("filing_period_mismatch")
        identities = tuple(
            sorted(
                {match_entity(context, expected) for context in document.facts.contexts},
                key=lambda entity: (entity.scheme, entity.identifier),
            )
        )
        archives.append(
            ArchiveReview(
                key=key,
                archive_sha256=document.facts.archive_sha256,
                entities=identities,
                mixed_entities=len(identities) > 1,
                filing_reasons=tuple(sorted(reasons)),
                metrics=tuple(
                    review_metric(document.facts, rule, proposal, expected, tuple(sorted(reasons)))
                    for rule in proposal.rules
                ),
            )
        )
    return MappingReview(
        source_manifest_sha256=sha256(source["manifest.json"]).hexdigest(),
        proposal_sha256=sha256(proposal_bytes).hexdigest(),
        inherited_reasons=manifest.reasons,
        archives=tuple(archives),
    )


def validate_mapping(files: Mapping[str, bytes], source: Mapping[str, bytes]) -> None:
    """Reject changed proposals, sidecars or original evidence by exact replay."""
    if set(files) != {"proposal.json", "review.json"}:
        raise ValueError("unexpected_mapping_files")
    result = evaluate_mapping(source, files["proposal.json"])
    if files["review.json"] != result.model_dump_json(indent=2).encode():
        raise ValueError("mapping_replay_mismatch")


def prepare_mapping(source: Path, proposal: Path, output: Path) -> MappingReview:
    """Atomically publish a review without copying raw evidence or credentials."""
    source, proposal, output = _safe_path(source), _safe_path(proposal), _safe_path(output)
    if source == output or source in output.parents or output in source.parents:
        raise ValueError("mapping_output_overlaps_source")
    source_files = read_bundle(source)
    proposal_bytes = proposal.read_bytes()
    result = evaluate_mapping(source_files, proposal_bytes)
    files = {"proposal.json": proposal_bytes, "review.json": result.model_dump_json(indent=2).encode()}
    publish_run_preparation(
        output.parent, output.name, files, validator=lambda saved: validate_mapping(saved, source_files)
    )
    return result
