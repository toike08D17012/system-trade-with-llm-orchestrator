"""Private P1 projections retain replayed price/FX values and acquisition limits."""

import json
from collections import Counter
from collections.abc import Mapping
from datetime import date
from hashlib import sha256

import yaml

from stock_research_llm_orchestrator.contracts.base import ArtifactReferenceV1
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.evidence import (
    ApplicablePeriodV1,
    CalculatedEvidenceV1,
    NormalizedEvidenceV1,
    RawEvidenceV1,
    SourceMetadataEvidenceV1,
)
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.policies import SourceApprovalV1
from stock_research_llm_orchestrator.preparation.dukascopy_fx import FxMetadataV2
from stock_research_llm_orchestrator.preparation.price_fx_run import PriceFxPreparationManifest, RunConversionRow
from stock_research_llm_orchestrator.sources.dukascopy.daily import DailySeries
from stock_research_llm_orchestrator.sources.yfinance.normalization import NormalizedPrices


def _bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, indent=2).encode()


def _source_records(
    files: Mapping[str, bytes],
    body_path: str,
    approval_path: str,
    retrieved: str,
    period: ApplicablePeriodV1,
    reference: str,
    timezone: str,
    provenance: dict[str, object],
) -> tuple[SourceMetadataEvidenceV1, RawEvidenceV1, dict[str, list[dict[str, str]]], dict[str, object]]:
    """Bind an exact retained payload and acquisition approval without a DB claim."""
    approval_body = files[approval_path]
    approval = SourceApprovalV1.model_validate(yaml.safe_load(approval_body))
    digest = sha256(files[body_path]).hexdigest()
    approval_ref = ArtifactReferenceV1(
        artifact_type="source_approval",
        artifact_id=approval.artifact_id,
        schema_id=approval.schema_id,
        schema_version=approval.schema_version,
        sha256=sha256(approval_body).hexdigest(),
    )
    metadata = {
        "layer": "source_metadata",
        "source_id": approval.source_id,
        "source_approval_reference": approval_ref.model_dump(mode="json"),
        "provider": approval.provider,
        "service": approval.service,
        "dataset": approval.dataset,
        "published_at": None,
        "first_available_at": None,
        "updated_at": None,
        "retrieved_at": retrieved,
        "applicable_period": period.model_dump(mode="json"),
        "timezone": timezone,
        "reference": reference,
        "content_sha256": digest,
        "data_version": digest,
    }
    locator = {"dependency": "price_fx", "path": body_path, "sha256": digest}
    payload = {"metadata": metadata, "acquisition": provenance, "locator": locator}
    source_id = "source-market-" + sha256(_bytes(payload)).hexdigest()
    source = SourceMetadataEvidenceV1.model_validate_json(_bytes({"evidence_id": source_id, **metadata}))
    payload_id = "market-payload-" + digest
    raw_payload = {
        "layer": "raw",
        "source_metadata_evidence_id": source_id,
        "payload_reference": ArtifactReferenceV1(
            artifact_type="raw_evidence_payload",
            artifact_id=payload_id,
            schema_id="detailed-analysis.raw-evidence-payload",
            schema_version=1,
            sha256=digest,
        ).model_dump(mode="json"),
        "content_sha256": digest,
        "data_version": digest,
    }
    raw = RawEvidenceV1.model_validate_json(
        _bytes({"evidence_id": "raw-market-" + sha256(_bytes(raw_payload)).hexdigest(), **raw_payload})
    )
    locators = {
        payload_id: [locator],
        approval.artifact_id: [{"dependency": "price_fx", "path": approval_path, "sha256": approval_ref.sha256}],
    }
    return source, raw, locators, payload


def build_price_fx_evidence(
    files: Mapping[str, bytes], limitations: tuple[str, ...]
) -> tuple[bytes, dict[str, object]]:
    """Project an already replayed run; never recalculate acceptance or conversion."""
    manifest = PriceFxPreparationManifest.model_validate_json(files["manifest.json"])
    native = "price-input/task.json" in files
    prefix = "price-input/" if native else "price-input/preparation/"
    approval_path = "acquisition-approvals/approvals/yfinance.yaml" if native else "price-input/approvals/yfinance.yaml"
    history = json.loads(files[prefix + "history-metadata.json"])
    approval = SourceApprovalV1.model_validate(yaml.safe_load(files[approval_path]))
    if (
        history["artifact_kind"] != "yfinance_returned_dataframe"
        or history["http_body_retained"] is not False
        or history["policy"] != "yfinance-native-history-v1"
        or history["source_approval_version"] != approval.approval_version
        or approval.source_id != "yfinance"
    ):
        raise ValueError("price_fx_evidence_native_acquisition_required")
    prices = NormalizedPrices.model_validate_json(files[prefix + "normalized.json"])
    fx_metadata = FxMetadataV2.model_validate_json(files["fx-input/metadata.json"])
    fx_series = DailySeries.model_validate_json(files["fx-input/normalized.json"])
    fx_rows = {r.on: r for r in fx_series.rows}
    if len(fx_rows) != len(fx_series.rows):
        raise ValueError("price_fx_evidence_fx_dates_ambiguous")
    records: list[SourceMetadataEvidenceV1 | RawEvidenceV1 | NormalizedEvidenceV1 | CalculatedEvidenceV1] = []
    payloads: dict[str, object] = {}
    locators: dict[str, list[dict[str, str]]] = {}
    source, raw, source_locators, provenance = _source_records(
        files,
        prefix + "prices.csv",
        approval_path,
        history["retrieved_at"],
        prices.requested_period,
        f"yfinance:{history['symbol']}:history",
        "Asia/Tokyo",
        {"history_metadata": history, "payload_kind": "library_dataframe_csv_export", "http_body_retained": False},
    )
    records.extend((source, raw))
    payloads[source.evidence_id] = provenance
    locators.update(source_locators)
    price_raw_id = raw.evidence_id
    fx_raw_ids = {}
    for receipt in sorted(fx_metadata.receipts, key=lambda r: r.year):
        path = f"fx-input/raw/{receipt.year}.bin"
        if receipt.year in fx_raw_ids or sha256(files[path]).hexdigest() != receipt.raw_reference.content_sha256:
            raise ValueError("price_fx_evidence_fx_source_invalid")
        period = ApplicablePeriodV1(
            start_date=max(fx_metadata.period_start, f"{receipt.year}-01-01"),
            end_date=min(fx_metadata.period_end, f"{receipt.year}-12-31"),
        )
        source, raw, source_locators, provenance = _source_records(
            files,
            path,
            "fx-input/approval.yaml",
            receipt.received_at,
            period,
            receipt.url,
            "UTC",
            {
                "receipt": receipt.model_dump(mode="json"),
                "observation_basis": fx_metadata.observation_basis,
                "requested_period": {"start_date": fx_metadata.period_start, "end_date": fx_metadata.period_end},
                "year_bucket": receipt.year,
                "runtime_database_rechecked": False,
            },
        )
        records.extend((source, raw))
        payloads[source.evidence_id] = provenance
        for key, entries in source_locators.items():
            for entry in entries:
                if entry not in locators.setdefault(key, []):
                    locators[key].append(entry)
        fx_raw_ids[receipt.year] = raw.evidence_id
    rows = tuple(RunConversionRow.model_validate_json(_bytes(r)) for r in json.loads(files["conversion.json"]))
    if tuple(r.on for r in rows) != manifest.requirements.price_dates:
        raise ValueError("price_fx_evidence_conversion_dates_invalid")
    context = {
        "manifest_sha256": sha256(files["manifest.json"]).hexdigest(),
        "checked_at": manifest.checked_at,
        "replay_input_hashes": manifest.files,
        "requirements": manifest.requirements.model_dump(mode="json"),
        "source_usage_approvals": {
            name: sha256(body).hexdigest() for name, body in files.items() if name.startswith("sources/")
        },
        "restricted_uses": manifest.restricted_uses,
    }
    context_hash = sha256(_bytes(context)).hexdigest()
    for row in rows:
        year = date.fromisoformat(row.on).year
        if year not in fx_raw_ids:
            raise ValueError("price_fx_evidence_fx_bucket_missing")
        normalized_ids = []
        row_period = {"start_date": row.on, "end_date": row.on}
        for metric, value, unit, raw_id, layer in (
            ("close_jpy", row.close_jpy, "JPY_per_share", price_raw_id, manifest.price),
            ("usd_jpy", row.usd_jpy, "JPY_per_USD", fx_raw_ids[year], manifest.fx),
        ):
            missing_reason = (
                None
                if value is not None
                else ("price_missing_or_unconfirmed" if metric == "close_jpy" else "fx_missing_or_unconfirmed")
            )
            observation = fx_rows.get(row.on)
            projected_value = str(value) if value is not None else None
            payload = {
                "metric": metric,
                "period": row_period,
                "value": projected_value,
                "missing_reason": missing_reason,
                "unit": unit,
                "input_evidence_ids": [raw_id],
                "normalization_logic_version": 1,
                "run_layer": layer.model_dump(mode="json"),
                "row_reasons": row.reasons,
                "context_sha256": context_hash,
                "source_normalized_sha256": sha256(
                    files[prefix + "normalized.json" if metric == "close_jpy" else "fx-input/normalized.json"]
                ).hexdigest(),
                "time_basis": "XTKS session unadjusted close"
                if metric == "close_jpy"
                else fx_metadata.observation_basis,
                "fx_observation": observation.model_dump(mode="json")
                if metric == "usd_jpy" and observation is not None
                else None,
            }
            digest = sha256(_bytes(payload)).hexdigest()
            normalized = NormalizedEvidenceV1(
                evidence_id="normalized-market-" + digest,
                layer="normalized",
                input_evidence_ids=(raw_id,),
                value=projected_value,
                missing_reason=missing_reason,
                unit=unit,
                content_sha256=digest,
                data_version=digest,
                normalization_logic_version=1,
            )
            records.append(normalized)
            payloads[normalized.evidence_id] = payload
            normalized_ids.append(normalized.evidence_id)
        if row.close_usd is None and not row.reasons:
            raise ValueError("price_fx_evidence_conversion_reason_missing")
        payload = {
            "metric": "close_usd",
            "period": row_period,
            "row": row.model_dump(mode="json"),
            "input_evidence_ids": normalized_ids,
            "unit": "USD_per_share",
            "calculation_logic_version": 1,
            "calculation": manifest.calculation,
            "precision": manifest.precision,
            "rounding_method": manifest.rounding,
            "context_sha256": context_hash,
            "conversion_sha256": sha256(files["conversion.json"]).hexdigest(),
        }
        digest = sha256(_bytes(payload)).hexdigest()
        calculated = CalculatedEvidenceV1(
            evidence_id="calculated-market-" + digest,
            layer="calculated",
            input_evidence_ids=tuple(normalized_ids),
            value=str(row.close_usd) if row.close_usd is not None else None,
            missing_reason=";".join(row.reasons) if row.close_usd is None else None,
            unit="USD_per_share",
            content_sha256=digest,
            data_version=digest,
            calculation_logic_version=1,
            rounding_method="ROUND_HALF_EVEN;precision=28;no_quantize",
        )
        records.append(calculated)
        payloads[calculated.evidence_id] = payload
    body = _bytes(
        {
            "version": 1,
            "kind": "internal-price-fx-p1-evidence-draft",
            "status": "pending",
            "analysis_ready": False,
            "task_id": manifest.task_id,
            "context": context,
            "context_sha256": context_hash,
            "records": [r.model_dump(mode="json") for r in sorted(records, key=lambda r: r.evidence_id)],
            "payloads": payloads,
            "artifact_locators": locators,
            "limitations": sorted(
                set(limitations)
                | set(manifest.limitations)
                | {
                    "not_a_frozen_evidence_set",
                    "no_external_agent_transfer",
                    "yfinance_dataframe_export_not_http_body",
                    "fx_runtime_database_not_rechecked",
                }
            ),
        }
    )
    return body, {"sha256": sha256(body).hexdigest(), "record_counts": dict(Counter(r.layer for r in records))}
