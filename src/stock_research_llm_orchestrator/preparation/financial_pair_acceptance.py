"""Adopt one pinned comparative period only after original/amended agreement."""

import json
from collections.abc import Mapping
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Literal

from stock_research_llm_orchestrator.contracts.base import StrictContractModel
from stock_research_llm_orchestrator.preparation.edinet_pair import PAIR_IDS, validate_pair_bundle
from stock_research_llm_orchestrator.preparation.financial_acceptance import FinancialAdoptionPolicy, adopt_metric
from stock_research_llm_orchestrator.preparation.financial_comparative import AnnualPeriod, ComparativePeriodValues
from stock_research_llm_orchestrator.preparation.financial_mapping import MappingProposal, MappingRule, review_metric
from stock_research_llm_orchestrator.preparation.fx_evidence import read_bundle
from stock_research_llm_orchestrator.preparation.market_revalidation import _safe_path
from stock_research_llm_orchestrator.preparation.price_fx_run import publish_run_preparation
from stock_research_llm_orchestrator.sources.edinet.xbrl_document import EdinetXbrlDocumentAdapter
from stock_research_llm_orchestrator.sources.edinet.xbrl_taxonomy import read_taxonomy_members, verify_taxonomy
from stock_research_llm_orchestrator.sources.protocol import BoundedSourceResponse


PAIR_POLICY_SHA256 = "78e02bb735216f52dbff1d6ff69d5c5e86024343006b13ac064d8a173d5463c0"


class PairAdoptionPolicy(StrictContractModel):
    """Exact pair and one fact period, never general amendment precedence."""

    version: Literal[1]
    original: FinancialAdoptionPolicy
    amended: FinancialAdoptionPolicy
    adopted_period: AnnualPeriod


def pair_proposal(policy: FinancialAdoptionPolicy) -> MappingProposal:
    """Use only the exact concept pinned by each reviewed taxonomy rule."""
    declarations = {d.href: d.concept for d in policy.taxonomy.declarations}
    return MappingProposal(
        start_date=policy.start_date,
        end_date=policy.end_date,
        rules=tuple(
            MappingRule(
                metric=r.metric,
                concepts=(declarations[r.taxonomy.concept_href],),
                evidence_references=(policy.approval_reference,),
            )
            for r in policy.rules
        ),
    )


def compare_pair_records(
    original: tuple[ComparativePeriodValues, ...], amended: tuple[ComparativePeriodValues, ...]
) -> None:
    """Do not select values when any required metric is absent or differs."""
    if tuple(r.period for r in original) != tuple(r.period for r in amended):
        raise ValueError("pair_adoption_period_mismatch")
    for left, right in zip(original, amended, strict=True):
        lvalues = {v.metric: v for v in left.values}
        for value in right.values:
            old = lvalues[value.metric]
            if (
                old.status != "accepted"
                or value.status != "accepted"
                or old.value is None
                or value.value is None
                or old.currency != value.currency
                or old.scope != value.scope
                or Decimal(old.value) != Decimal(value.value)
            ):
                raise ValueError("pair_adoption_values_unresolved")


def evaluate_pair_acceptance(pair: Mapping[str, bytes], policy_bytes: bytes) -> dict[str, bytes]:
    """Replay raw, taxonomy and both periods before adopting six comparative facts."""
    if sha256(policy_bytes).hexdigest() != PAIR_POLICY_SHA256:
        raise ValueError("pair_adoption_policy_unapproved")
    policy = PairAdoptionPolicy.model_validate_json(policy_bytes)
    validate_pair_bundle(pair)
    policies = (policy.original, policy.amended)
    if tuple(p.document_id for p in policies) != PAIR_IDS:
        raise ValueError("pair_adoption_document_scope")
    periods = (
        policy.adopted_period,
        AnnualPeriod(start_date=policy.amended.start_date, end_date=policy.amended.end_date),
    )
    results = []
    proof_sets = {}
    for source in policies:
        body = pair[f"raw/{source.document_id}/body.bin"]
        if sha256(body).hexdigest() != source.archive_sha256:
            raise ValueError("pair_adoption_archive_mismatch")
        response = BoundedSourceResponse(
            physical_attempt_id="pair-adoption-replay",
            body=body,
            sha256=source.archive_sha256,
            media_type="application/octet-stream",
            encoding="binary",
        )
        document = EdinetXbrlDocumentAdapter().parse(response)
        proposal = pair_proposal(source)
        if sha256(proposal.model_dump_json(indent=2).encode()).hexdigest() != source.proposal_sha256:
            raise ValueError("pair_adoption_proposal_mismatch")
        members = read_taxonomy_members(response, source.taxonomy)
        proofs = {r.metric: verify_taxonomy(members, source.taxonomy, r.taxonomy) for r in source.rules}
        if not all(p.verified for p in proofs.values()):
            raise ValueError("pair_adoption_taxonomy_unresolved")
        proof_sets[source.document_id] = {k: p.model_dump(mode="json") for k, p in proofs.items()}
        records = []
        for period in periods:
            current = proposal.model_copy(update={"start_date": period.start_date, "end_date": period.end_date})
            values = tuple(
                adopt_metric(
                    document.facts,
                    review_metric(document.facts, r, current, source.edinet_code),
                    proofs[r.metric],
                    period.start_date,
                    period.end_date,
                )
                for r in current.rules
            )
            records.append(ComparativePeriodValues(period=period, values=values))
        results.append(tuple(records))
    compare_pair_records(results[0], results[1])
    acquisitions = json.loads(pair["acquisitions.json"])
    task = json.loads(pair["task.json"])
    adopted = results[1][0]
    files = {
        "policy.json": policy_bytes,
        "values.json": json.dumps([adopted.model_dump(mode="json")], sort_keys=True, indent=2).encode(),
        "taxonomy.json": json.dumps(proof_sets, sort_keys=True, indent=2).encode(),
    }
    manifest = {
        "kind": "local-financial-pair-acceptance",
        "version": 1,
        "status": "pending",
        "analysis_ready": False,
        "accepted_count": 6,
        "task_id": task["task_id"],
        "security_code": policy.amended.security_code,
        "edinet_code": policy.amended.edinet_code,
        "document_id": policy.amended.document_id,
        "archive_sha256": policy.amended.archive_sha256,
        "reporting_period": periods[1].model_dump(mode="json"),
        "fact_period": policy.adopted_period.model_dump(mode="json"),
        "retrieved_at": {a["key"]: a["retrieved_at"] for a in acquisitions},
        "matched_metric_period_count": 12,
        "reporting_basis": "as_reported_in_amended_source",
        "limitations": policy.amended.limitations,
        "standard_schema_sha256": policy.amended.standard_schema_sha256,
        "hashes": {name: sha256(body).hexdigest() for name, body in files.items()},
        "dependency_hashes": {name: sha256(body).hexdigest() for name, body in pair.items()},
    }
    files["manifest.json"] = json.dumps(manifest, sort_keys=True, indent=2).encode()
    return files


def validate_pair_acceptance(files: Mapping[str, bytes], pair: Mapping[str, bytes]) -> None:
    """Require the whole adopted artifact to match an offline recomputation."""
    if dict(files) != evaluate_pair_acceptance(pair, files["policy.json"]):
        raise ValueError("pair_adoption_replay_mismatch")


def prepare_pair_acceptance(pair: Path, policy: Path, output: Path) -> None:
    """Keep source artifacts immutable and values local."""
    pair, policy, output = (_safe_path(p) for p in (pair, policy, output))
    if any(output == p or output in p.parents or p in output.parents for p in (pair, policy)):
        raise ValueError("pair_adoption_output_overlap")
    if output.exists():
        raise FileExistsError("pair_adoption_output_exists")
    source = read_bundle(pair)
    files = evaluate_pair_acceptance(source, policy.read_bytes())
    publish_run_preparation(
        output.parent, output.name, files, validator=lambda saved: validate_pair_acceptance(saved, source)
    )
