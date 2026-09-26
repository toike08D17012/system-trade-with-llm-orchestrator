"""Offline per-task preparation with independently evaluated price and FX layers."""

import fcntl
import json
import os
from collections.abc import Mapping
from datetime import datetime, time, timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from hashlib import sha256
from pathlib import Path
from typing import Literal
from uuid import uuid4

import yaml
from pydantic import Field

from stock_research_llm_orchestrator.contracts.base import Identifier, Sha256Hex, StrictContractModel
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.policies import (
    DetailedAnalysisPolicyV1,
    SourceApprovalV1,
    SourceProfileV1,
)
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.task import DetailedAnalysisTaskV1
from stock_research_llm_orchestrator.preparation.dukascopy_fx import FxMetadataV2, validate_dukascopy_fx
from stock_research_llm_orchestrator.preparation.fx_evidence import read_bundle
from stock_research_llm_orchestrator.preparation.market_evidence import validate_market_preparation
from stock_research_llm_orchestrator.preparation.market_revalidation import _read_file, _safe_path, _timestamp
from stock_research_llm_orchestrator.preparation.price_acceptance import (
    LIMITATIONS,
    RESTRICTED_USES,
    PriceAcceptanceIndex,
    _evaluate,
    validate_price_acceptance,
)
from stock_research_llm_orchestrator.preparation.run_requirements import (
    PROFILE_EFFECTIVE_ON,
    TOKYO,
    RunRequirements,
    calendar_inputs,
    resolve_requirements,
    session_end,
)
from stock_research_llm_orchestrator.preparation.storage import PreparationValidator, publish_preparation
from stock_research_llm_orchestrator.sources.dukascopy.daily import DailySeries
from stock_research_llm_orchestrator.sources.yfinance.normalization import NormalizedPrices


SOURCE_VERSIONS = {"jpx": 1, "yfinance": 3, "dukascopy": 1}
NATIVE_REUSE_POLICY = (
    b'{"policy":"yfinance-native-history-v1","source_approval_version":3,"legacy_http_profile_applicable":false}'
)


class RunLayerResult(StrictContractModel):
    """A layer's readiness never implies full analysis readiness."""

    status: Literal["ready_with_limitations", "pending"]
    reasons: tuple[str, ...]
    missing_dates: tuple[str, ...]
    available_end: str | None


class RunConversionRow(StrictContractModel):
    """Projected source decimals and explicit unconfirmed or missing FX states."""

    on: str
    close_jpy: Decimal | None
    usd_jpy: Decimal | None
    close_usd: Decimal | None
    reasons: tuple[str, ...]


class PreparationEvidenceReference(StrictContractModel):
    """Internal references point only to retained immutable files or other records."""

    evidence_id: Identifier
    layer: Literal["source_metadata", "raw", "normalized", "calculated"]
    paths: tuple[str, ...]
    input_evidence_ids: tuple[Identifier, ...]
    publication_generation: Identifier


class PriceFxPreparationManifest(StrictContractModel):
    """A price/FX slice, distinct from frozen EvidenceSet and ExecutionManifest."""

    version: Literal[1] = 1
    kind: Literal["internal-price-fx-run-preparation"] = "internal-price-fx-run-preparation"
    analysis_ready: Literal[False] = False
    task_id: Identifier
    checked_at: str
    publication_generation: Identifier
    requirements: RunRequirements
    status: Literal["ready_with_limitations", "pending"]
    price: RunLayerResult
    fx: RunLayerResult
    conversion: RunLayerResult
    reused_from_task_id: Identifier | None
    reuse_revalidated: bool
    input_locations: dict[str, str]
    records: tuple[PreparationEvidenceReference, ...]
    files: dict[str, Sha256Hex]
    excluded_price_dates: tuple[str, ...]
    excluded_fx_dates: dict[str, str]
    limitations: tuple[str, ...]
    restricted_uses: tuple[str, ...] = RESTRICTED_USES
    calculation: Literal["unadjusted-close-jpy-divided-by-usdjpy-v1"] = "unadjusted-close-jpy-divided-by-usdjpy-v1"
    precision: Literal[28] = 28
    rounding: Literal["ROUND_HALF_EVEN"] = "ROUND_HALF_EVEN"
    converted_count: int = Field(ge=0)


def _subset(files: Mapping[str, bytes], prefix: str) -> dict[str, bytes]:
    return {name.removeprefix(prefix): body for name, body in files.items() if name.startswith(prefix)}


def _source_reasons(files: Mapping[str, bytes], checked: datetime) -> dict[str, tuple[str, ...]]:
    result: dict[str, tuple[str, ...]] = {}
    for source, version in SOURCE_VERSIONS.items():
        a_bytes = files[f"sources/{source}/approval.yaml"]
        approval = SourceApprovalV1.model_validate_json(json.dumps(yaml.safe_load(a_bytes)))
        if approval.source_id != source or approval.approval_version != version:
            raise ValueError("run_source_binding_mismatch")
        if source == "yfinance":
            if files["sources/yfinance/native-policy.json"] != NATIVE_REUSE_POLICY:
                raise ValueError("run_native_policy_mismatch")
            enabled = True
        else:
            profile = SourceProfileV1.model_validate_json(
                json.dumps(yaml.safe_load(files[f"sources/{source}/profile.yaml"]))
            )
            if (
                profile.source_id != source
                or profile.profile_version != version
                or profile.source_approval_reference.sha256 != sha256(a_bytes).hexdigest()
                or profile.source_approval_reference.artifact_id != approval.artifact_id
            ):
                raise ValueError("run_source_binding_mismatch")
            enabled = profile.enabled and profile.source_approval_status == "approved"
        day = checked.astimezone(TOKYO).date().isoformat()
        usable = (
            approval.status == "approved"
            and approval.online_use_allowed
            and enabled
            and approval.effective_on is not None
            and approval.recheck_due_on is not None
            and approval.effective_on <= day < approval.recheck_due_on
        )
        result[source] = () if usable else (f"{source}_reuse_policy_unavailable",)
    return result


def _price_inputs(files: Mapping[str, bytes]) -> tuple[dict[str, bytes], DetailedAnalysisTaskV1]:
    original = _subset(files, "price-input/")
    if "task.json" in original:
        validate_market_preparation(original)
        price = {f"preparation/{name}": body for name, body in original.items()}
        for name in ("calendar.json", "calendar-metadata.json", "research-note.md"):
            price[name] = files[f"requirements-input/{name}"]
        # Acquisition approval version differs from the current library policy version.
        price.update(_subset(files, "acquisition-approvals/"))
    else:
        validate_price_acceptance(original)
        price = {name: body for name, body in original.items() if name != "index.json"}
    task = DetailedAnalysisTaskV1.model_validate_json(price["preparation/task.json"])
    return price, task


def _layer(reasons: list[str], missing: list[str], available: list[str]) -> RunLayerResult:
    return RunLayerResult(
        status="pending" if reasons or missing else "ready_with_limitations",
        reasons=tuple(sorted(set(reasons))),
        missing_dates=tuple(missing),
        available_end=max(available, default=None),
    )


def _evaluate_run(files: Mapping[str, bytes]) -> tuple[PriceFxPreparationManifest, bytes]:
    task = DetailedAnalysisTaskV1.model_validate_json(files["task.json"])
    control = RunControl.model_validate_json(files["control.json"])
    checked = _timestamp(control.checked_at)
    if _timestamp(task.task_accepted_at) > checked:
        raise ValueError("run_task_accepted_after_check")
    evaluation_policy = DetailedAnalysisPolicyV1.model_validate_json(
        json.dumps(yaml.safe_load(files["evaluation-policy.yaml"]))
    )
    if (
        evaluation_policy.policy_version != task.evaluation_policy_version
        or _timestamp(evaluation_policy.effective_at) > checked
    ):
        raise ValueError("run_evaluation_policy_mismatch")
    req = resolve_requirements(_subset(files, "requirements-input/"), control.checked_at)
    policy = _source_reasons(files, checked)
    price_inputs, source_task = _price_inputs(files)
    if source_task.security != task.security or source_task.market != task.market:
        raise ValueError("run_price_security_mismatch")
    if _timestamp(source_task.task_accepted_at) > checked:
        raise ValueError("run_source_task_from_future")
    source_files = _subset(price_inputs, "preparation/")
    prices = NormalizedPrices.model_validate_json(source_files["normalized.json"])
    history = json.loads(source_files["history-metadata.json"])
    retrieved = _timestamp(history["retrieved_at"])
    local = checked.astimezone(TOKYO)
    completed = local.date() if local.time() >= time(15, 30) else local.date() - timedelta(days=1)
    evaluation = _evaluate(price_inputs, control.checked_at, completed_through=completed)
    if "index.json" in _subset(files, "price-input/") and "task.json" not in _subset(files, "price-input/"):
        old = PriceAcceptanceIndex.model_validate_json(files["price-input/index.json"])
        if _timestamp(old.checked_at) > checked:
            raise ValueError("run_price_validation_from_future")
    fx_files = _subset(files, "fx-input/")
    validate_dukascopy_fx(fx_files)
    fx_meta = FxMetadataV2.model_validate_json(fx_files["metadata.json"])
    if _timestamp(fx_meta.requested_at) > checked or any(_timestamp(r.received_at) > checked for r in fx_meta.receipts):
        raise ValueError("run_fx_from_future")
    fx_series = DailySeries.model_validate_json(fx_files["normalized.json"])
    price_reasons = list(req.reasons) + list(policy["jpx"]) + list(policy["yfinance"])
    # Whole-input quality remains conservative even when projecting a smaller period.
    for condition in evaluation.conditions:
        if not condition.passed:
            price_reasons.extend(condition.reasons)
    price_map = {row.on: row for row in prices.rows}
    fx_map = {row.on: row for row in fx_series.rows}
    good_price: dict[str, Decimal] = {}
    good_fx: dict[str, Decimal] = {}
    missing_price, missing_fx = [], []
    for day in req.price_dates:
        row = price_map.get(day)
        if row is None or row.close is None or not row.close.is_finite() or row.close <= 0:
            missing_price.append(day)
            continue
        # For historical dates, require next-day retrieval rather than retroactively applying today's hours.
        if day >= PROFILE_EFFECTIVE_ON.isoformat():
            closed = session_end(day)
        else:
            closed = datetime.combine(datetime.fromisoformat(day).date() + timedelta(days=1), time(), TOKYO)
        if retrieved < closed:
            missing_price.append(day)
            price_reasons.append("price_retrieved_before_session_completion")
            continue
        good_price[day] = row.close
    fx_reasons = list(req.reasons) + list(policy["dukascopy"])
    for day in req.fx_dates:
        fx_row = fx_map.get(day)
        if (
            fx_row is None
            or not fx_row.confirmed
            or _timestamp(fx_row.interval_end) > checked
            or not fx_meta.period_start <= day <= fx_meta.period_end
        ):
            missing_fx.append(day)
        else:
            good_fx[day] = fx_row.close
    if not req.fx_dates:
        fx_reasons.append("no_required_confirmed_fx_date")
    # Source availability is useful even when the calendar cannot resolve run requirements.
    available_prices = list(good_price)
    available_fx = list(good_fx)
    if req.status == "pending":
        available_prices = [
            r.on
            for r in prices.rows
            if r.close is not None
            and r.close.is_finite()
            and r.close > 0
            and datetime.combine(datetime.fromisoformat(r.on).date() + timedelta(days=1), time(), TOKYO) <= retrieved
        ]
        available_fx = [
            r.on
            for r in fx_series.rows
            if r.confirmed
            and _timestamp(r.interval_end) <= checked
            and fx_meta.period_start <= r.on <= fx_meta.period_end
        ]
    price_layer = _layer(price_reasons, missing_price, available_prices)
    fx_layer = _layer(fx_reasons, missing_fx, available_fx)
    rows = []
    converted_days = []
    for day in req.price_dates:
        reasons = []
        price = good_price.get(day)
        fx = good_fx.get(day)
        if price is None:
            reasons.append("price_missing_or_unconfirmed")
        if day not in req.fx_dates:
            reasons.append("fx_interval_unfinished")
        elif fx is None:
            reasons.append("fx_missing_or_unconfirmed")
        if price_reasons:
            reasons.append("price_not_accepted")
        if policy["dukascopy"]:
            reasons.append("fx_reuse_not_approved")
        converted = None
        if not reasons and price is not None and fx is not None:
            with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
                converted = price / fx
            converted_days.append(day)
        rows.append(RunConversionRow(on=day, close_jpy=price, usd_jpy=fx, close_usd=converted, reasons=tuple(reasons)))
    conversion_missing = [day for day in req.fx_dates if day not in converted_days]
    conversion_layer = _layer(price_reasons + fx_reasons, conversion_missing, converted_days)
    converted_bytes = json.dumps([row.model_dump(mode="json") for row in rows], ensure_ascii=False, indent=2).encode()
    retained = {name: body for name, body in files.items() if name not in {"manifest.json", "conversion.json"}}
    retained["conversion.json"] = converted_bytes
    allowed = {"task.json", "control.json", "conversion.json", "evaluation-policy.yaml"}
    allowed.update(
        f"requirements-input/{name}"
        for name in ("calendar.json", "calendar-metadata.json", "research-note.md", "market-profile.yaml")
    )
    allowed.update(f"sources/{source}/approval.yaml" for source in SOURCE_VERSIONS)
    allowed.update(f"sources/{source}/profile.yaml" for source in ("jpx", "dukascopy"))
    allowed.add("sources/yfinance/native-policy.json")
    allowed.update(f"acquisition-approvals/approvals/{source}.yaml" for source in ("jpx", "yfinance"))
    allowed.update(name for name in retained if name.startswith(("price-input/", "fx-input/")))
    if set(retained) != allowed:
        raise ValueError("run_file_inventory_mismatch")
    # Each original input bundle validates its own exact inventory above.
    price_prefix = "price-input/preparation/" if "price-input/preparation/task.json" in retained else "price-input/"
    records = (
        PreparationEvidenceReference(
            evidence_id="source-metadata",
            layer="source_metadata",
            paths=tuple(
                sorted(
                    name
                    for name in retained
                    if name.startswith(("sources/", "requirements-input/", "acquisition-approvals/"))
                )
            )
            + (price_prefix + "history-metadata.json", "fx-input/metadata.json", "evaluation-policy.yaml"),
            input_evidence_ids=(),
            publication_generation=control.publication_generation,
        ),
        PreparationEvidenceReference(
            evidence_id="price-table",
            layer="normalized",
            paths=(price_prefix + "prices.csv",),
            input_evidence_ids=("source-metadata",),
            publication_generation=control.publication_generation,
        ),
        PreparationEvidenceReference(
            evidence_id="price-normalized",
            layer="normalized",
            paths=(price_prefix + "normalized.json",),
            input_evidence_ids=("price-table",),
            publication_generation=control.publication_generation,
        ),
        PreparationEvidenceReference(
            evidence_id="fx-raw",
            layer="raw",
            paths=tuple(sorted(name for name in retained if name.startswith("fx-input/raw/"))),
            input_evidence_ids=("source-metadata",),
            publication_generation=control.publication_generation,
        ),
        PreparationEvidenceReference(
            evidence_id="fx-normalized",
            layer="normalized",
            paths=("fx-input/normalized.json",),
            input_evidence_ids=("fx-raw",),
            publication_generation=control.publication_generation,
        ),
        PreparationEvidenceReference(
            evidence_id="conversion",
            layer="calculated",
            paths=("conversion.json",),
            input_evidence_ids=("price-normalized", "fx-normalized"),
            publication_generation=control.publication_generation,
        ),
    )
    manifest = PriceFxPreparationManifest(
        task_id=task.task_id,
        checked_at=control.checked_at,
        publication_generation=control.publication_generation,
        requirements=req,
        status="ready_with_limitations"
        if all(layer.status == "ready_with_limitations" for layer in (price_layer, fx_layer, conversion_layer))
        else "pending",
        price=price_layer,
        fx=fx_layer,
        conversion=conversion_layer,
        reused_from_task_id=source_task.task_id if source_task.task_id != task.task_id else None,
        reuse_revalidated=source_task.task_id != task.task_id,
        input_locations=control.input_locations,
        records=records,
        files={name: sha256(body).hexdigest() for name, body in sorted(retained.items())},
        excluded_price_dates=tuple(day for day in price_map if day not in req.price_dates),
        excluded_fx_dates={
            day: "unfinished_candle" if not row.confirmed else "outside_required_conversion_dates"
            for day, row in fx_map.items()
            if day not in req.fx_dates or not row.confirmed
        },
        limitations=tuple(item for item in LIMITATIONS if item != "fixed_period_not_current_run_freshness")
        + (
            "price_fx_slice_only",
            "no_revision_check_against_provider",
            "stock_and_fx_closes_not_simultaneous",
            "calendar_validated_under_owner_local_schedule_policy",
        ),
        converted_count=len(converted_days),
    )
    return manifest, converted_bytes


class RunControl(StrictContractModel):
    """Fixed replay time and destination-independent publication identity."""

    checked_at: str
    publication_generation: Identifier
    input_locations: dict[str, str]


def validate_price_fx_run(files: Mapping[str, bytes]) -> None:
    """Recompute requirements, statuses, references and decimals from retained bytes."""
    actual = PriceFxPreparationManifest.model_validate_json(files["manifest.json"])
    expected, conversion = _evaluate_run(files)
    if actual != expected or files["conversion.json"] != conversion:
        raise ValueError("run_manifest_or_conversion_mismatch")


def prepare_price_fx_run(
    *,
    task: DetailedAnalysisTaskV1,
    checked_at: datetime,
    config: Path,
    calendar: Path,
    calendar_metadata: Path,
    research_note: Path,
    prices: Path,
    fx: Path,
    output: Path,
) -> PriceFxPreparationManifest:
    """Publish one offline run slice; caller exclusively owns the output parent."""
    paths = tuple(map(_safe_path, (config, calendar, calendar_metadata, research_note, prices, fx)))
    destination = _safe_path(output)
    if any(destination == p or destination in p.parents or p in destination.parents for p in paths):
        raise ValueError("run_output_overlaps_input")
    if destination.exists():
        raise FileExistsError("run_output_exists")
    config, calendar, calendar_metadata, research_note, prices, fx = paths
    files = {
        f"requirements-input/{name}": body
        for name, body in calendar_inputs(config, calendar, calendar_metadata, research_note).items()
    }
    for source, version in SOURCE_VERSIONS.items():
        for category, name in (("source-approvals", "approval"), ("source-profiles", "profile")):
            if source == "yfinance" and name == "profile":
                files["sources/yfinance/native-policy.json"] = NATIVE_REUSE_POLICY
                continue
            files[f"sources/{source}/{name}.yaml"] = _read_file(config / category / source / f"v{version}.yaml")
    for source, version in (("jpx", 1), ("yfinance", 3)):
        files[f"acquisition-approvals/approvals/{source}.yaml"] = _read_file(
            config / "source-approvals" / source / f"v{version}.yaml"
        )
    files.update({f"price-input/{name}": body for name, body in read_bundle(prices).items()})
    files.update({f"fx-input/{name}": body for name, body in read_bundle(fx).items()})
    files["evaluation-policy.yaml"] = _read_file(
        config / "policies/detailed-analysis" / f"v{task.evaluation_policy_version}.yaml"
    )
    files["task.json"] = task.model_dump_json(indent=2).encode()
    files["control.json"] = (
        RunControl(
            checked_at=checked_at.isoformat(),
            publication_generation=f"run-{uuid4().hex}",
            input_locations={"prices": str(prices), "fx": str(fx)},
        )
        .model_dump_json(indent=2)
        .encode()
    )
    manifest, conversion = _evaluate_run(files)
    files["conversion.json"] = conversion
    files["manifest.json"] = manifest.model_dump_json(indent=2).encode()
    publish_run_preparation(destination.parent, destination.name, files, validator=validate_price_fx_run)
    return manifest


def publish_run_preparation(
    root: Path, name: str, files: Mapping[str, bytes], *, validator: PreparationValidator
) -> None:
    """Reject competing publishers using the same private preparation parent."""
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("run_publication_root_busy") from None
        publish_preparation(root, name, files, validator=validator)
    finally:
        os.close(descriptor)
