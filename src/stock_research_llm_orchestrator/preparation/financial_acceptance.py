"""Owner-approved local financial values with replayable taxonomy provenance."""

import json
from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from stock_research_llm_orchestrator.contracts.base import Sha256Hex, StrictContractModel
from stock_research_llm_orchestrator.preparation.financial_disclosure import FinancialInput, FinancialManifest
from stock_research_llm_orchestrator.preparation.financial_mapping import (
    METRICS,
    MappingProposal,
    MappingReview,
    Metric,
    MetricReview,
    validate_mapping,
)
from stock_research_llm_orchestrator.preparation.fx_evidence import read_bundle
from stock_research_llm_orchestrator.preparation.market_revalidation import _safe_path
from stock_research_llm_orchestrator.preparation.price_fx_run import publish_run_preparation
from stock_research_llm_orchestrator.sources.edinet.xbrl_document import EdinetXbrlDocument
from stock_research_llm_orchestrator.sources.edinet.xbrl_facts import EdinetXbrlFactSet, EdinetXbrlQName
from stock_research_llm_orchestrator.sources.edinet.xbrl_taxonomy import (
    TaxonomyProof,
    TaxonomyRule,
    TaxonomySpec,
    read_taxonomy_members,
    verify_taxonomy,
)
from stock_research_llm_orchestrator.sources.protocol import BoundedSourceResponse


APPROVED_POLICY_SHA256 = "7874467ef5032e5dcfa58a7403259f08c93222b7fb91ac73d95856c944c4d46f"

PRIOR_APPROVED_POLICY_SHA256 = "6c9e1e2793cfbaba7fef5b96052727ba0105c2a63510946f9cdd6ab47e6eae0e"


class AcceptedMappingRule(StrictContractModel):
    """One metric's policy-reviewed taxonomy requirements."""

    metric: Metric
    taxonomy: TaxonomyRule


class FinancialAdoptionPolicy(StrictContractModel):
    """Pinned owner approval for one issuer, filing, archive and period."""

    version: Literal[1] = 1
    status: Literal["approved"]
    approval_reference: str
    security_code: str
    edinet_code: str
    document_id: str
    archive_sha256: Sha256Hex
    proposal_sha256: Sha256Hex
    start_date: date
    end_date: date
    taxonomy: TaxonomySpec
    rules: tuple[AcceptedMappingRule, ...]
    standard_schema_sha256: dict[str, Sha256Hex] = Field(default_factory=dict)
    reporting_basis: Literal["as_reported_in_source"] | None = None
    limitations: tuple[str, ...] = ()

    @model_validator(mode="after")
    def require_six_metrics(self) -> FinancialAdoptionPolicy:
        """Require complete unambiguous coverage of the approved initial scope."""
        if sorted(rule.metric for rule in self.rules) != sorted(METRICS) or self.start_date > self.end_date:
            raise ValueError("invalid_financial_adoption_scope")
        return self


class AcceptedFactReference(StrictContractModel):
    """Preserve every equal-valued source fact reference."""

    ordinal: int
    member: str
    context_id: str
    unit_id: str
    decimals: str | None
    precision: str | None


class FinancialValue(StrictContractModel):
    """Local-only value; this model must never be printed by the CLI."""

    metric: Metric
    status: Literal["accepted", "unaccepted"]
    value: str | None
    currency: Literal["JPY"] = "JPY"
    scope: Literal["consolidated"] = "consolidated"
    concept: EdinetXbrlQName | None
    start_date: date | None
    end_date: date
    references: tuple[AcceptedFactReference, ...]
    reasons: tuple[str, ...]


class FinancialAcceptanceManifest(StrictContractModel):
    """Individual adoption never satisfies whole-run evidence readiness."""

    version: Literal[1] = 1
    kind: Literal["local-financial-acceptance"] = "local-financial-acceptance"
    status: Literal["pending"] = "pending"
    analysis_ready: Literal[False] = False
    archive_sha256: Sha256Hex
    source_manifest_sha256: Sha256Hex
    review_sha256: Sha256Hex
    accepted_count: int
    inherited_reasons: tuple[str, ...]
    hashes: dict[str, Sha256Hex]
    limitations: tuple[str, ...] = (
        "single_filing_period_only",
        "no_external_agent_transfer",
        "not_a_frozen_evidence_set",
    )


def adopt_metric(
    facts: EdinetXbrlFactSet, review: MetricReview, proof: TaxonomyProof, start: date, end: date
) -> FinancialValue:
    """Normalize only verified, comparable facts; do not infer scope or repair conflicts."""
    reasons = set(proof.reasons)
    if not proof.verified:
        reasons.add("taxonomy_proof_unresolved")
    period_type = "instant" if review.metric in {"assets", "equity"} else "duration"
    if proof.period_type != period_type:
        reasons.add("taxonomy_period_type_mismatch")
    if review.status not in {"candidate", "duplicate"}:
        reasons.add(f"candidate_{review.status}")
    if not review.comparable_ordinals:
        reasons.add("no_comparable_facts")
    by_ordinal = {fact.ordinal: fact for fact in facts.facts}
    selected = [by_ordinal[ordinal] for ordinal in review.comparable_ordinals]
    if any(
        EdinetXbrlQName(namespace=f.concept_namespace, local_name=f.concept_local_name) != proof.concept
        for f in selected
    ):
        reasons.add("taxonomy_fact_concept_mismatch")
    numbers = {Decimal(f.value) for f in selected if f.value is not None}
    if len(numbers) != 1 or any(f.is_nil for f in selected):
        reasons.add("no_unique_numeric_value")
    references = tuple(
        AcceptedFactReference(
            ordinal=f.ordinal,
            member=f.source_member_path,
            context_id=f.context_ref,
            unit_id=f.unit_ref or "",
            decimals=f.decimals,
            precision=f.precision,
        )
        for f in selected
    )
    normalized = None
    if not reasons:
        number = next(iter(numbers))
        normalized = format(number, "f") if number else "0"
        if "." in normalized:
            normalized = normalized.rstrip("0").rstrip(".")
    return FinancialValue(
        metric=review.metric,
        status="unaccepted" if reasons else "accepted",
        value=normalized,
        concept=proof.concept,
        start_date=start if period_type == "duration" else None,
        end_date=end,
        references=references,
        reasons=tuple(sorted(reasons)),
    )


def _json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False).encode()


def evaluate_acceptance(
    source: Mapping[str, bytes], review_files: Mapping[str, bytes], policy_bytes: bytes
) -> dict[str, bytes]:
    """Recompute adoption from raw evidence and an exact, explicitly approved policy."""
    if sha256(policy_bytes).hexdigest() not in {APPROVED_POLICY_SHA256, PRIOR_APPROVED_POLICY_SHA256}:
        raise ValueError("financial_adoption_policy_not_approved")
    policy = FinancialAdoptionPolicy.model_validate_json(policy_bytes)
    validate_mapping(review_files, source)
    proposal = MappingProposal.model_validate_json(review_files["proposal.json"])
    review = MappingReview.model_validate_json(review_files["review.json"])
    inputs = FinancialInput.model_validate_json(source["inputs.json"])
    manifest = FinancialManifest.model_validate_json(source["manifest.json"])
    if (
        inputs.issuer is None
        or (inputs.issuer.security_code, inputs.issuer.edinet_code) != (policy.security_code, policy.edinet_code)
        or sha256(review_files["proposal.json"]).hexdigest() != policy.proposal_sha256
        or (proposal.start_date, proposal.end_date) != (policy.start_date, policy.end_date)
    ):
        raise ValueError("financial_adoption_binding_mismatch")
    archive_keys = {
        key
        for filing in manifest.filings
        if filing.document.document_id == policy.document_id
        for key in filing.archive_keys
    }
    archives = [
        archive
        for archive in review.archives
        if archive.key in archive_keys and archive.archive_sha256 == policy.archive_sha256
    ]
    if len(archives) != 1:
        raise ValueError("financial_adoption_archive_mismatch")
    archive = archives[0]
    acquisition = next(item for item in inputs.acquisitions if item.key == archive.key)
    response = BoundedSourceResponse(
        physical_attempt_id=acquisition.publication.physical_attempt_id,
        body=source[f"raw/{archive.key}/body.bin"],
        sha256=policy.archive_sha256,
        media_type="application/octet-stream",
        encoding="binary",
    )
    members = read_taxonomy_members(response, policy.taxonomy)
    document = EdinetXbrlDocument.model_validate_json(json.dumps(json.loads(source["candidates.json"])[archive.key]))
    metrics = {metric.metric: metric for metric in archive.metrics}
    proofs = {rule.metric: verify_taxonomy(members, policy.taxonomy, rule.taxonomy) for rule in policy.rules}
    values = tuple(
        adopt_metric(document.facts, metrics[rule.metric], proofs[rule.metric], policy.start_date, policy.end_date)
        for rule in policy.rules
    )
    files = {
        "policy.json": policy_bytes,
        "taxonomy.json": _json({metric: proof.model_dump(mode="json") for metric, proof in proofs.items()}),
        "values.json": _json([value.model_dump(mode="json") for value in values]),
        "diagnostic.json": _json(
            [
                {
                    "metric": value.metric,
                    "status": value.status,
                    "reasons": value.reasons,
                    "reference_count": len(value.references),
                }
                for value in values
            ]
        ),
    }
    if policy.reporting_basis is not None:
        files["provenance.json"] = _json(
            {
                "source_document_id": policy.document_id,
                "source_archive_sha256": policy.archive_sha256,
                "reporting_period": {
                    "start_date": policy.start_date.isoformat(),
                    "end_date": policy.end_date.isoformat(),
                },
                "reporting_basis": policy.reporting_basis,
                "standard_schema_sha256": policy.standard_schema_sha256,
                "limitations": policy.limitations,
            }
        )
    result = FinancialAcceptanceManifest(
        archive_sha256=policy.archive_sha256,
        source_manifest_sha256=sha256(source["manifest.json"]).hexdigest(),
        review_sha256=sha256(review_files["review.json"]).hexdigest(),
        accepted_count=sum(value.status == "accepted" for value in values),
        inherited_reasons=manifest.reasons,
        hashes={name: sha256(body).hexdigest() for name, body in files.items()},
    )
    if policy.limitations:
        result = result.model_copy(update={"limitations": (*result.limitations, *policy.limitations)})
    files["manifest.json"] = result.model_dump_json(indent=2).encode()
    return files


def validate_acceptance(files: Mapping[str, bytes], source: Mapping[str, bytes], review: Mapping[str, bytes]) -> None:
    """Detect any changed value, diagnostic, proof, policy or referenced source."""
    if dict(files) != evaluate_acceptance(source, review, files["policy.json"]):
        raise ValueError("financial_adoption_replay_mismatch")


def prepare_acceptance(source: Path, review: Path, policy: Path, output: Path) -> FinancialAcceptanceManifest:
    """Publish private local financial values atomically without network or credentials."""
    source, review, policy, output = (_safe_path(path) for path in (source, review, policy, output))
    if any(output == path or output in path.parents or path in output.parents for path in (source, review)):
        raise ValueError("financial_adoption_output_overlap")
    if output.exists():
        raise FileExistsError("financial_adoption_output_exists")
    source_files, review_files = read_bundle(source), read_bundle(review)
    files = evaluate_acceptance(source_files, review_files, policy.read_bytes())
    publish_run_preparation(
        output.parent,
        output.name,
        files,
        validator=lambda saved: validate_acceptance(saved, source_files, review_files),
    )
    return FinancialAcceptanceManifest.model_validate_json(files["manifest.json"])
