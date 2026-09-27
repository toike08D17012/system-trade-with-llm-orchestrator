"""Pinned comparative periods from one revalidated annual filing, kept local."""

import json
from collections.abc import Mapping
from datetime import date
from hashlib import sha256
from pathlib import Path
from typing import Literal

from pydantic import model_validator

from stock_research_llm_orchestrator.contracts.base import Sha256Hex, StrictContractModel
from stock_research_llm_orchestrator.preparation.financial_acceptance import (
    FinancialAdoptionPolicy,
    FinancialValue,
    adopt_metric,
    validate_acceptance,
)
from stock_research_llm_orchestrator.preparation.financial_mapping import METRICS, MappingProposal, review_metric
from stock_research_llm_orchestrator.preparation.fx_evidence import read_bundle
from stock_research_llm_orchestrator.preparation.market_revalidation import _safe_path
from stock_research_llm_orchestrator.preparation.price_fx_run import publish_run_preparation
from stock_research_llm_orchestrator.sources.edinet.xbrl_document import EdinetXbrlDocument
from stock_research_llm_orchestrator.sources.edinet.xbrl_taxonomy import TaxonomyProof


APPROVED_COMPARATIVE_POLICY_SHA256 = "eaf1740c4c717eefcc82921ce6ffb1b98bcbb0804aafb775fa26c34ecfe5790e"


class AnnualPeriod(StrictContractModel):
    """Explicit annual coverage target, independent of instant fact dates."""

    start_date: date
    end_date: date

    @model_validator(mode="after")
    def require_order(self) -> AnnualPeriod:
        """Reject reversed periods."""
        if self.start_date > self.end_date:
            raise ValueError("comparative_period_reversed")
        return self


class ComparativePolicy(StrictContractModel):
    """Exact owner approval; the base policy binds QNames and taxonomy proofs."""

    version: Literal[1] = 1
    status: Literal["approved"]
    approval_reference: str
    base_policy_sha256: Sha256Hex
    security_code: str
    edinet_code: str
    document_id: str
    archive_sha256: Sha256Hex
    proposal_sha256: Sha256Hex
    reporting_period: AnnualPeriod
    periods: tuple[AnnualPeriod, ...]
    required_annual_periods: tuple[AnnualPeriod, ...]

    @model_validator(mode="after")
    def require_scope(self) -> ComparativePolicy:
        """Require unique bounded periods including the reporting period."""
        if (
            len(set(self.periods)) != len(self.periods)
            or len(set(self.required_annual_periods)) != 5
            or len(self.required_annual_periods) != 5
            or self.reporting_period not in self.periods
            or not set(self.periods).issubset(self.required_annual_periods)
            or any(p.end_date > self.reporting_period.end_date for p in self.required_annual_periods)
        ):
            raise ValueError("comparative_period_scope_invalid")
        return self


class ComparativePeriodValues(StrictContractModel):
    """Local-only six-metric record; never print this model."""

    period: AnnualPeriod
    values: tuple[FinancialValue, ...]

    @model_validator(mode="after")
    def require_metrics(self) -> ComparativePeriodValues:
        """Require six distinct metrics with matching fact periods."""
        if sorted(v.metric for v in self.values) != sorted(METRICS):
            raise ValueError("comparative_metric_keys_invalid")
        if any(
            v.end_date != self.period.end_date
            or v.start_date != (None if v.metric in {"assets", "equity"} else self.period.start_date)
            for v in self.values
        ):
            raise ValueError("comparative_value_period_mismatch")
        return self


class AnnualCoverage(StrictContractModel):
    """Value-free completeness of one required annual period."""

    period: AnnualPeriod
    status: Literal["complete", "partial", "missing"]
    accepted_count: int
    missing_metrics: tuple[str, ...]


def annual_coverage(
    required: tuple[AnnualPeriod, ...], records: tuple[ComparativePeriodValues, ...]
) -> tuple[AnnualCoverage, ...]:
    """Count a period only once and only when all six distinct metrics are accepted."""
    by_period = {record.period: record for record in records}
    if len(by_period) != len(records) or not set(by_period).issubset(required):
        raise ValueError("comparative_coverage_period_invalid")
    result = []
    for period in required:
        record = by_period.get(period)
        accepted = {v.metric for v in record.values if v.status == "accepted"} if record else set()
        missing = tuple(metric for metric in METRICS if metric not in accepted)
        result.append(
            AnnualCoverage(
                period=period,
                status="complete" if not missing else "partial" if accepted else "missing",
                accepted_count=len(accepted),
                missing_metrics=missing,
            )
        )
    return tuple(result)


class ComparativeManifest(StrictContractModel):
    """Metadata-only replay contract for one pinned comparative sidecar."""

    version: Literal[1] = 1
    kind: Literal["local-financial-comparative-acceptance"] = "local-financial-comparative-acceptance"
    status: Literal["pending"] = "pending"
    analysis_ready: Literal[False] = False
    accepted_count: int
    annual_coverage: tuple[AnnualCoverage, ...]
    complete_annual_count: int
    partial_annual_count: int
    hashes: dict[str, Sha256Hex]
    dependency_hashes: dict[str, dict[str, Sha256Hex]]
    limitations: tuple[str, ...] = (
        "single_archive_explicit_periods_only",
        "latest_published_period_unconfirmed",
        "no_external_agent_transfer",
        "not_a_frozen_evidence_set",
    )


def _json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False).encode()


def evaluate_comparative(
    source: Mapping[str, bytes],
    review: Mapping[str, bytes],
    adoption: Mapping[str, bytes],
    policy_bytes: bytes,
) -> dict[str, bytes]:
    """Replay current-period eligibility and taxonomy before considering comparisons."""
    if sha256(policy_bytes).hexdigest() != APPROVED_COMPARATIVE_POLICY_SHA256:
        raise ValueError("comparative_policy_not_approved")
    policy = ComparativePolicy.model_validate_json(policy_bytes)
    validate_acceptance(adoption, source, review)
    base = FinancialAdoptionPolicy.model_validate_json(adoption["policy.json"])
    if (
        sha256(adoption["policy.json"]).hexdigest() != policy.base_policy_sha256
        or any(
            getattr(base, field) != getattr(policy, field)
            for field in ("security_code", "edinet_code", "document_id", "archive_sha256", "proposal_sha256")
        )
        or (base.start_date, base.end_date) != (policy.reporting_period.start_date, policy.reporting_period.end_date)
    ):
        raise ValueError("comparative_policy_binding_mismatch")
    documents = [
        EdinetXbrlDocument.model_validate_json(json.dumps(item))
        for item in json.loads(source["candidates.json"]).values()
    ]
    selected = [doc for doc in documents if doc.facts.archive_sha256 == policy.archive_sha256]
    if len(selected) != 1:
        raise ValueError("comparative_archive_ambiguous")
    facts = selected[0].facts
    # validate_acceptance above recomputed these proofs against the saved raw archive.
    proofs = {
        metric: TaxonomyProof.model_validate_json(json.dumps(proof))
        for metric, proof in json.loads(adoption["taxonomy.json"]).items()
    }
    proposal = MappingProposal.model_validate_json(review["proposal.json"])
    records = []
    diagnostics = []
    for period in policy.periods:
        period_proposal = proposal.model_copy(update={"start_date": period.start_date, "end_date": period.end_date})
        reviews = tuple(review_metric(facts, rule, period_proposal, policy.edinet_code) for rule in proposal.rules)
        values = tuple(
            adopt_metric(facts, metric, proofs[metric.metric], period.start_date, period.end_date) for metric in reviews
        )
        record = ComparativePeriodValues(period=period, values=values)
        records.append(record)
        diagnostics.append(
            {"period": period.model_dump(mode="json"), "metrics": [r.model_dump(mode="json") for r in reviews]}
        )
        if period == policy.reporting_period:
            original = tuple(
                FinancialValue.model_validate_json(json.dumps(value)) for value in json.loads(adoption["values.json"])
            )
            if {v.metric: v for v in values} != {v.metric: v for v in original}:
                raise ValueError("comparative_current_adoption_mismatch")
    coverage = annual_coverage(policy.required_annual_periods, tuple(records))
    files = {
        "policy.json": policy_bytes,
        "values.json": _json([record.model_dump(mode="json") for record in records]),
        "diagnostic.json": _json(diagnostics),
        "taxonomy.json": adoption["taxonomy.json"],
    }
    manifest = ComparativeManifest(
        accepted_count=sum(c.accepted_count for c in coverage),
        annual_coverage=coverage,
        complete_annual_count=sum(c.status == "complete" for c in coverage),
        partial_annual_count=sum(c.status == "partial" for c in coverage),
        hashes={name: sha256(body).hexdigest() for name, body in files.items()},
        dependency_hashes={
            label: {name: sha256(body).hexdigest() for name, body in dependency.items()}
            for label, dependency in (("financial", source), ("review", review), ("adoption", adoption))
        },
    )
    files["manifest.json"] = manifest.model_dump_json(indent=2).encode()
    return files


def validate_comparative(
    files: Mapping[str, bytes], source: Mapping[str, bytes], review: Mapping[str, bytes], adoption: Mapping[str, bytes]
) -> None:
    """Compare the complete immutable sidecar with a fresh offline evaluation."""
    if dict(files) != evaluate_comparative(source, review, adoption, files["policy.json"]):
        raise ValueError("comparative_replay_mismatch")


def prepare_comparative(source: Path, review: Path, adoption: Path, policy: Path, output: Path) -> ComparativeManifest:
    """Publish a separate private bundle, without rewriting current-period evidence."""
    source, review, adoption, policy, output = (_safe_path(p) for p in (source, review, adoption, policy, output))
    if any(output == p or output in p.parents or p in output.parents for p in (source, review, adoption, policy)):
        raise ValueError("comparative_output_overlap")
    if output.exists():
        raise FileExistsError("comparative_output_exists")
    source_files, review_files, adoption_files = (read_bundle(p) for p in (source, review, adoption))
    files = evaluate_comparative(source_files, review_files, adoption_files, policy.read_bytes())
    publish_run_preparation(
        output.parent,
        output.name,
        files,
        validator=lambda saved: validate_comparative(saved, source_files, review_files, adoption_files),
    )
    return ComparativeManifest.model_validate_json(files["manifest.json"])
