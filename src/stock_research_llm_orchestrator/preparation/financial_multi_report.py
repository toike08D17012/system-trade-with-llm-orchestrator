"""Join two approved reports without implying a common freshness check."""

import json
from collections.abc import Mapping
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.task import DetailedAnalysisTaskV1
from stock_research_llm_orchestrator.preparation.financial_acceptance import (
    FinancialAcceptanceManifest,
    FinancialAdoptionPolicy,
)
from stock_research_llm_orchestrator.preparation.financial_comparative import (
    AnnualCoverage,
    AnnualPeriod,
    ComparativeManifest,
    ComparativePeriodValues,
    ComparativePolicy,
    validate_comparative,
)
from stock_research_llm_orchestrator.preparation.financial_disclosure import FinancialManifest
from stock_research_llm_orchestrator.preparation.financial_mapping import METRICS
from stock_research_llm_orchestrator.preparation.financial_pair_acceptance import validate_pair_acceptance
from stock_research_llm_orchestrator.preparation.financial_run import (
    FinancialComparativeRunManifest,
    evaluate_financial_run,
)
from stock_research_llm_orchestrator.preparation.fx_evidence import read_bundle
from stock_research_llm_orchestrator.preparation.interim_ir import InterimPolicy, validate_interim
from stock_research_llm_orchestrator.preparation.market_revalidation import _safe_path, _timestamp
from stock_research_llm_orchestrator.preparation.price_fx_run import publish_run_preparation


DEPENDENCIES = (
    "financial",
    "review",
    "adoption",
    "comparative",
    "price_fx",
    "additional_financial",
    "additional_review",
    "additional_adoption",
    "additional_comparative",
)


def merge_coverage(
    required: tuple[AnnualPeriod, ...], reports: tuple[tuple[ComparativePeriodValues, ...], ...]
) -> tuple[AnnualCoverage, ...]:
    """Reject conflicting accepted values; count equal repeated facts only once."""
    accepted: dict[tuple[AnnualPeriod, str], tuple[Decimal, str, str]] = {}
    for records in reports:
        if len({r.period for r in records}) != len(records):
            raise ValueError("multi_report_duplicate_period")
        for record in records:
            if record.period not in required:
                raise ValueError("multi_report_period_outside_window")
            for value in record.values:
                if value.status != "accepted":
                    continue
                if value.value is None:
                    raise ValueError("multi_report_missing_accepted_value")
                key = (record.period, value.metric)
                normalized = (Decimal(value.value), value.currency, value.scope)
                if not normalized[0].is_finite():
                    raise ValueError("multi_report_nonfinite_value")
                if key in accepted and accepted[key] != normalized:
                    raise ValueError("multi_report_conflicting_value")
                accepted[key] = normalized
    result = []
    for period in required:
        missing = tuple(metric for metric in METRICS if (period, metric) not in accepted)
        count = len(METRICS) - len(missing)
        result.append(
            AnnualCoverage(
                period=period,
                status="complete" if not missing else "partial" if count else "missing",
                accepted_count=count,
                missing_metrics=missing,
            )
        )
    return tuple(result)


def evaluate_multi_report(dependencies: Mapping[str, Mapping[str, bytes]]) -> dict[str, bytes]:
    """Revalidate every source and retain per-source task, time and limitations."""
    pair_keys = {"pair", "pair_adoption"}
    has_pair = pair_keys <= dependencies.keys()
    interim_keys = {"interim", "interim_source"}
    has_interim = interim_keys <= dependencies.keys()
    if set(dependencies) != set(DEPENDENCIES) | (pair_keys if has_pair else set()) | (
        interim_keys if has_interim else set()
    ):
        raise ValueError("multi_report_dependency_set_invalid")
    d = dependencies
    primary = evaluate_financial_run(d["financial"], d["review"], d["adoption"], d["price_fx"], d["comparative"])
    base_run = FinancialComparativeRunManifest.model_validate_json(primary["manifest.json"])
    validate_comparative(
        d["additional_comparative"], d["additional_financial"], d["additional_review"], d["additional_adoption"]
    )
    tasks = [DetailedAnalysisTaskV1.model_validate_json(d[p + "financial"]["task.json"]) for p in ("", "additional_")]
    scope = ("security", "market", "analysis_horizons", "evaluation_policy_version", "constraints")
    if any(getattr(tasks[0], field) != getattr(tasks[1], field) for field in scope):
        raise ValueError("multi_report_task_scope_mismatch")
    if d["financial"]["evaluation-policy.yaml"] != d["additional_financial"]["evaluation-policy.yaml"]:
        raise ValueError("multi_report_evaluation_policy_mismatch")
    policies = [
        FinancialAdoptionPolicy.model_validate_json(d[p + "adoption"]["policy.json"]) for p in ("", "additional_")
    ]
    if (policies[0].security_code, policies[0].edinet_code) != (policies[1].security_code, policies[1].edinet_code):
        raise ValueError("multi_report_issuer_mismatch")
    if policies[0].archive_sha256 == policies[1].archive_sha256:
        raise ValueError("multi_report_duplicate_archive")
    required = ComparativePolicy.model_validate_json(d["comparative"]["policy.json"]).required_annual_periods
    reports = []
    sources = []
    limitations = set(base_run.limitations)
    for prefix, task, policy in zip(("", "additional_"), tasks, policies, strict=True):
        records = tuple(
            ComparativePeriodValues.model_validate_json(json.dumps(r))
            for r in json.loads(d[prefix + "comparative"]["values.json"])
        )
        reports.append(records)
        manifest = FinancialManifest.model_validate_json(d[prefix + "financial"]["manifest.json"])
        comparison = ComparativeManifest.model_validate_json(d[prefix + "comparative"]["manifest.json"])
        adoption_manifest = FinancialAcceptanceManifest.model_validate_json(d[prefix + "adoption"]["manifest.json"])
        source_limits = tuple(
            sorted(set(comparison.limitations) | set(manifest.limitations) | set(adoption_manifest.limitations))
        )
        limitations.update(source_limits)
        sources.append(
            {
                "dependency": prefix + "financial",
                "task_id": task.task_id,
                "checked_at": manifest.checked_at,
                "document_id": policy.document_id,
                "archive_sha256": policy.archive_sha256,
                "reporting_basis": policy.reporting_basis or "as_reported_in_source",
                "limitations": source_limits,
                "reasons": manifest.reasons,
                "metrics": [
                    {
                        "period": r.period.model_dump(mode="json"),
                        "metric": v.metric,
                        "status": v.status,
                        "reference_count": len(v.references),
                        "reasons": v.reasons,
                    }
                    for r in records
                    for v in r.values
                ],
            }
        )
    if has_pair:
        validate_pair_acceptance(d["pair_adoption"], d["pair"])
        pair_task = DetailedAnalysisTaskV1.model_validate_json(d["pair"]["task.json"])
        pair_manifest = json.loads(d["pair_adoption"]["manifest.json"])
        if any(getattr(tasks[0], field) != getattr(pair_task, field) for field in scope):
            raise ValueError("multi_report_pair_task_scope_mismatch")
        if d["financial"]["evaluation-policy.yaml"] != d["pair"]["evaluation-policy.yaml"]:
            raise ValueError("multi_report_pair_policy_mismatch")
        if (pair_manifest["security_code"], pair_manifest["edinet_code"]) != (
            policies[0].security_code,
            policies[0].edinet_code,
        ):
            raise ValueError("multi_report_pair_issuer_mismatch")
        pair_records = tuple(
            ComparativePeriodValues.model_validate_json(json.dumps(r))
            for r in json.loads(d["pair_adoption"]["values.json"])
        )
        reports.append(pair_records)
        limitations.update(pair_manifest["limitations"])
        sources.append(
            {
                "dependency": "pair",
                "adoption": pair_manifest,
                "metrics": [
                    {
                        "period": r.period.model_dump(mode="json"),
                        "metric": v.metric,
                        "status": v.status,
                        "reference_count": len(v.references),
                        "reasons": v.reasons,
                    }
                    for r in pair_records
                    for v in r.values
                ],
            }
        )
    coverage = merge_coverage(required, tuple(reports))
    inputs = json.dumps(
        {key: {name: sha256(body).hexdigest() for name, body in files.items()} for key, files in d.items()},
        sort_keys=True,
        indent=2,
    ).encode()
    limitations.update({"source_specific_check_times", "no_combined_freshness_check", "not_a_frozen_evidence_set"})
    result = {
        "version": 1,
        "kind": "internal-multi-report-financial-run-preparation",
        "status": "pending",
        "analysis_ready": False,
        "primary_run": base_run.model_dump(mode="json"),
        "sources": sources,
        "annual_coverage": [c.model_dump(mode="json") for c in coverage],
        "accepted_count": sum(c.accepted_count for c in coverage),
        "complete_annual_count": sum(c.status == "complete" for c in coverage),
        "partial_annual_count": sum(c.status == "partial" for c in coverage),
        "inputs_sha256": sha256(inputs).hexdigest(),
        "limitations": sorted(limitations),
    }
    if has_interim:
        validate_interim(d["interim"], d["interim_source"])
        interim_policy = InterimPolicy.model_validate_json(d["interim"]["policy.json"])
        if (interim_policy.security_code, interim_policy.edinet_code) != (
            policies[0].security_code,
            policies[0].edinet_code,
        ):
            raise ValueError("multi_report_interim_issuer_mismatch")
        if any(doc.published_on > _timestamp(base_run.checked_at).date() for doc in interim_policy.documents):
            raise ValueError("multi_report_interim_future_publication")
        interim_manifest = json.loads(d["interim"]["manifest.json"])
        result["interim_coverage"] = interim_manifest["interim_coverage"]
        result["interim_accepted_count"] = interim_manifest["accepted_count"]
        result["complete_interim_count"] = interim_manifest["complete_interim_count"]
        sources.append({"dependency": "interim", "adoption": interim_manifest})
        result["limitations"] = sorted(
            limitations | set(interim_manifest["limitations"]) | {"interim_operator_evidence_not_task_bound"}
        )
    return {"inputs.json": inputs, "manifest.json": json.dumps(result, sort_keys=True, indent=2).encode()}


def validate_multi_report(files: Mapping[str, bytes], dependencies: Mapping[str, Mapping[str, bytes]]) -> None:
    """Replay only explicitly supplied dependencies."""
    if dict(files) != evaluate_multi_report(dependencies):
        raise ValueError("multi_report_replay_mismatch")


def prepare_multi_report(paths: Mapping[str, Path], output: Path) -> None:
    """Publish a separate immutable metadata-only bundle."""
    output = _safe_path(output)
    paths = {key: _safe_path(path) for key, path in paths.items()}
    if any(output == p or output in p.parents or p in output.parents for p in paths.values()):
        raise ValueError("multi_report_output_overlap")
    if output.exists():
        raise FileExistsError("multi_report_output_exists")
    dependencies = {key: read_bundle(path) for key, path in paths.items()}
    files = evaluate_multi_report(dependencies)
    publish_run_preparation(
        output.parent, output.name, files, validator=lambda saved: validate_multi_report(saved, dependencies)
    )
