"""Value-free run preparation joining immutable local financial evidence."""

import json
from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path
from typing import Literal

from stock_research_llm_orchestrator.contracts.base import Sha256Hex, StrictContractModel
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.task import DetailedAnalysisTaskV1
from stock_research_llm_orchestrator.preparation.financial_acceptance import FinancialValue, validate_acceptance
from stock_research_llm_orchestrator.preparation.financial_comparative import (
    AnnualCoverage,
    ComparativeManifest,
    ComparativePeriodValues,
    validate_comparative,
)
from stock_research_llm_orchestrator.preparation.financial_disclosure import FinancialManifest
from stock_research_llm_orchestrator.preparation.financial_mapping import Metric
from stock_research_llm_orchestrator.preparation.fx_evidence import read_bundle
from stock_research_llm_orchestrator.preparation.market_revalidation import _safe_path, _timestamp
from stock_research_llm_orchestrator.preparation.price_fx_run import (
    PriceFxPreparationManifest,
    RunLayerResult,
    publish_run_preparation,
    validate_price_fx_run,
)


class FinancialMetricSummary(StrictContractModel):
    """Expose adoption state and period without copying the financial number."""

    metric: Metric
    status: Literal["accepted", "unaccepted"]
    start_date: str | None
    end_date: str
    reference_count: int
    reasons: tuple[str, ...]


class PriceFxRunSummary(StrictContractModel):
    """Preserve individual price/FX gaps and use restrictions."""

    status: Literal["ready_with_limitations", "pending"]
    price: RunLayerResult
    fx: RunLayerResult
    conversion: RunLayerResult
    limitations: tuple[str, ...]
    restricted_uses: tuple[str, ...]


class FinancialRunManifest(StrictContractModel):
    """A local run slice, not an analysis-ready frozen evidence set."""

    version: Literal[1] = 1
    kind: Literal["internal-financial-run-preparation"] = "internal-financial-run-preparation"
    status: Literal["pending"] = "pending"
    analysis_ready: Literal[False] = False
    task_id: str
    security_code: str
    checked_at: str
    accepted_count: int
    metrics: tuple[FinancialMetricSummary, ...]
    price_fx: PriceFxRunSummary | None
    reasons: tuple[str, ...]
    historical_reasons: tuple[str, ...]
    inputs_sha256: Sha256Hex
    limitations: tuple[str, ...] = ("local_only", "dependencies_required_for_replay", "not_a_frozen_evidence_set")


class FinancialComparativeRunManifest(StrictContractModel):
    """Version two adds fact coverage without changing version-one replay bytes."""

    version: Literal[2] = 2
    kind: Literal["internal-financial-run-preparation"] = "internal-financial-run-preparation"
    status: Literal["pending"] = "pending"
    analysis_ready: Literal[False] = False
    task_id: str
    security_code: str
    checked_at: str
    accepted_count: int
    metrics: tuple[FinancialMetricSummary, ...]
    price_fx: PriceFxRunSummary | None
    reasons: tuple[str, ...]
    historical_reasons: tuple[str, ...]
    inputs_sha256: Sha256Hex
    annual_coverage: tuple[AnnualCoverage, ...]
    complete_annual_count: int
    partial_annual_count: int
    filing_annual_periods: tuple[tuple[str, str], ...]
    limitations: tuple[str, ...]


def resolve_price_fx(financial: Mapping[str, bytes], supplied: Mapping[str, bytes] | None) -> dict[str, bytes] | None:
    """Select one exact price/FX dependency and require full task/time agreement."""
    embedded = {
        name.removeprefix("price-fx/"): body for name, body in financial.items() if name.startswith("price-fx/")
    }
    if embedded and supplied is not None and embedded != dict(supplied):
        raise ValueError("financial_run_price_fx_conflict")
    selected = dict(supplied) if supplied is not None else embedded
    if not selected:
        if supplied is not None:
            raise ValueError("financial_run_empty_price_fx")
        return None
    validate_price_fx_run(selected)
    manifest = PriceFxPreparationManifest.model_validate_json(selected["manifest.json"])
    original = FinancialManifest.model_validate_json(financial["manifest.json"])
    if DetailedAnalysisTaskV1.model_validate_json(financial["task.json"]) != DetailedAnalysisTaskV1.model_validate_json(
        selected["task.json"]
    ) or _timestamp(manifest.checked_at) != _timestamp(original.checked_at):
        raise ValueError("financial_run_price_fx_task_or_time_mismatch")
    return selected


def evaluate_financial_run(
    financial: Mapping[str, bytes],
    review: Mapping[str, bytes],
    adoption: Mapping[str, bytes],
    price_fx: Mapping[str, bytes] | None = None,
    comparative: Mapping[str, bytes] | None = None,
) -> dict[str, bytes]:
    """Replay all dependencies before constructing a number-free aggregate."""
    if comparative is None:
        validate_acceptance(adoption, financial, review)
    else:
        validate_comparative(comparative, financial, review, adoption)
    if "provenance.json" in adoption:
        raise ValueError("financial_source_report_run_integration_unapproved")
    original = FinancialManifest.model_validate_json(financial["manifest.json"])
    task = DetailedAnalysisTaskV1.model_validate_json(financial["task.json"])
    values = tuple(
        FinancialValue.model_validate_json(json.dumps(value)) for value in json.loads(adoption["values.json"])
    )
    if comparative is not None:
        records = tuple(
            ComparativePeriodValues.model_validate_json(json.dumps(record))
            for record in json.loads(comparative["values.json"])
        )
        values = tuple(value for record in records for value in record.values)
    metrics = tuple(
        FinancialMetricSummary(
            metric=value.metric,
            status=value.status,
            start_date=value.start_date.isoformat() if value.start_date is not None else None,
            end_date=value.end_date.isoformat(),
            reference_count=len(value.references),
            reasons=value.reasons,
        )
        for value in values
    )
    accepted_count = sum(value.status == "accepted" for value in values)
    reasons = set(original.reasons)
    reasons.discard("financial_mapping_unimplemented")
    reasons.add("financial_mapping_partial" if accepted_count else "financial_mapping_unaccepted")
    selected = resolve_price_fx(financial, price_fx)
    price_summary = None
    dependencies: dict[str, Mapping[str, bytes]] = {
        "financial": financial,
        "mapping_review": review,
        "adoption": adoption,
    }
    if selected is not None:
        dependencies["price_fx"] = selected
        price = PriceFxPreparationManifest.model_validate_json(selected["manifest.json"])
        price_summary = PriceFxRunSummary(
            status=price.status,
            price=price.price,
            fx=price.fx,
            conversion=price.conversion,
            limitations=price.limitations,
            restricted_uses=price.restricted_uses,
        )
        reasons.discard("price_fx_not_connected")
        if price.status == "pending":
            reasons.add("price_fx_pending")
    else:
        reasons.add("price_fx_not_connected")
    if comparative is not None:
        dependencies["comparative"] = comparative
    hashes = {
        key: {name: sha256(body).hexdigest() for name, body in files.items()} for key, files in dependencies.items()
    }
    inputs = json.dumps(hashes, sort_keys=True, indent=2).encode()
    result = FinancialRunManifest(
        task_id=task.task_id,
        security_code=task.security.security_code,
        checked_at=original.checked_at,
        accepted_count=accepted_count,
        metrics=metrics,
        price_fx=price_summary,
        reasons=tuple(sorted(reasons)),
        historical_reasons=original.reasons,
        inputs_sha256=sha256(inputs).hexdigest(),
    )
    if comparative is not None:
        coverage = ComparativeManifest.model_validate_json(comparative["manifest.json"])
        current = result.model_dump()
        current.update(
            version=2,
            annual_coverage=coverage.annual_coverage,
            complete_annual_count=coverage.complete_annual_count,
            partial_annual_count=coverage.partial_annual_count,
            filing_annual_periods=original.annual_periods,
            limitations=(*result.limitations, *coverage.limitations),
        )
        extended = FinancialComparativeRunManifest.model_validate(current)
        return {"inputs.json": inputs, "manifest.json": extended.model_dump_json(indent=2).encode()}
    return {"inputs.json": inputs, "manifest.json": result.model_dump_json(indent=2).encode()}


def validate_financial_run(
    files: Mapping[str, bytes],
    financial: Mapping[str, bytes],
    review: Mapping[str, bytes],
    adoption: Mapping[str, bytes],
    price_fx: Mapping[str, bytes] | None = None,
    comparative: Mapping[str, bytes] | None = None,
) -> None:
    """Require explicit dependency arguments; never follow paths read from a manifest."""
    if dict(files) != evaluate_financial_run(financial, review, adoption, price_fx, comparative):
        raise ValueError("financial_run_replay_mismatch")


def prepare_financial_run(
    financial: Path,
    review: Path,
    adoption: Path,
    output: Path,
    price_fx: Path | None = None,
    comparative: Path | None = None,
) -> FinancialRunManifest | FinancialComparativeRunManifest:
    """Publish a private, immutable reference bundle without modifying dependencies."""
    paths = [_safe_path(path) for path in (financial, review, adoption)]
    if price_fx is not None:
        paths.append(_safe_path(price_fx))
    comparative_path = _safe_path(comparative) if comparative is not None else None
    if comparative_path is not None:
        paths.append(comparative_path)
    output = _safe_path(output)
    if any(output == path or output in path.parents or path in output.parents for path in paths):
        raise ValueError("financial_run_output_overlaps_dependency")
    if output.exists():
        raise FileExistsError("financial_run_output_exists")
    source_files, review_files, adoption_files = (read_bundle(path) for path in paths[:3])
    price_files = read_bundle(paths[3]) if price_fx is not None else None
    comparative_files = read_bundle(comparative_path) if comparative_path is not None else None
    files = evaluate_financial_run(source_files, review_files, adoption_files, price_files, comparative_files)
    publish_run_preparation(
        output.parent,
        output.name,
        files,
        validator=lambda saved: validate_financial_run(
            saved, source_files, review_files, adoption_files, price_files, comparative_files
        ),
    )
    if comparative is not None:
        return FinancialComparativeRunManifest.model_validate_json(files["manifest.json"])
    return FinancialRunManifest.model_validate_json(files["manifest.json"])
