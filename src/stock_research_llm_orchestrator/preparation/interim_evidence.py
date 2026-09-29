"""Private P1 IR records keep operator adoption separate from runtime acquisition."""

import json
from collections import Counter
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path

import yaml

from stock_research_llm_orchestrator.contracts.base import ArtifactReferenceV1
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.evidence import (
    ApplicablePeriodV1,
    NormalizedEvidenceV1,
    RawEvidenceV1,
    SourceMetadataEvidenceV1,
)
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.policies import SourceApprovalV1
from stock_research_llm_orchestrator.preparation.financial_mapping import METRICS
from stock_research_llm_orchestrator.preparation.fx_evidence import read_bundle
from stock_research_llm_orchestrator.preparation.interim_ir import InterimPolicy
from stock_research_llm_orchestrator.preparation.interim_ir_runtime import IrRuntimeManifest
from stock_research_llm_orchestrator.sources.toyota_ir_pdf import IrMetric


def _bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, indent=2).encode()


def build_interim_evidence(
    policy: InterimPolicy,
    adoption: Mapping[str, bytes],
    operator_source: Mapping[str, bytes],
    acquisition: IrRuntimeManifest,
    runtime_files: Mapping[str, bytes],
    raw_root: Path,
    limitations: tuple[str, ...],
) -> tuple[bytes, dict[str, object]]:
    """Map already replayed adoption and DB-verified acquisition to an unfrozen draft.

    Runtime approval governs the runtime download only. Matching PDF bytes do
    not replace historical operator receipts or grant them runtime provenance.
    """
    values = json.loads(adoption["values.json"])
    keys = {d.key for d in policy.documents}
    if (
        len(values) != len(keys)
        or {v["document"] for v in values} != keys
        or len(acquisition.receipts) != len(keys)
        or {r.key for r in acquisition.receipts} != keys
        or acquisition.kind != "toyota-ir-runtime-acquisition"
    ):
        raise ValueError("interim_evidence_document_scope_invalid")
    approval_body = runtime_files["approval.yaml"]
    approval_hash = sha256(approval_body).hexdigest()
    if approval_hash != acquisition.hashes["approval.yaml"]:
        raise ValueError("interim_evidence_approval_hash_mismatch")
    approval = SourceApprovalV1.model_validate(yaml.safe_load(approval_body))
    approval_ref = ArtifactReferenceV1(
        artifact_type="source_approval",
        artifact_id=approval.artifact_id,
        schema_id=approval.schema_id,
        schema_version=approval.schema_version,
        sha256=approval_hash,
    )
    records: list[SourceMetadataEvidenceV1 | RawEvidenceV1 | NormalizedEvidenceV1] = []
    payloads: dict[str, object] = {}
    locators = {
        approval.artifact_id: [{"dependency": "interim_runtime", "path": "approval.yaml", "sha256": approval_hash}]
    }
    by_document = {v["document"]: v for v in values}
    receipts = {r.key: r for r in acquisition.receipts}
    values_hash = sha256(adoption["values.json"]).hexdigest()
    policy_hash = sha256(adoption["policy.json"]).hexdigest()
    for document in sorted(policy.documents, key=lambda d: d.key):
        receipt = receipts[document.key]
        ref = receipt.raw_reference
        operator_path = f"{document.key}/body.pdf"
        operator_receipt_path = f"{document.key}/receipt.json"
        relative_path = f"{ref.task_id}/{ref.relative_path}/body.bin"
        raw = read_bundle(raw_root / ref.task_id / ref.relative_path)
        if (
            ref.task_id != acquisition.task_id
            or ref.content_sha256 != document.sha256
            or sha256(raw["body.bin"]).hexdigest() != document.sha256
            or sha256(operator_source[operator_path]).hexdigest() != document.sha256
        ):
            raise ValueError("interim_evidence_raw_hash_mismatch")
        operator_receipt = json.loads(operator_source[operator_receipt_path])
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
            "retrieved_at": receipt.received_at,
            "applicable_period": ApplicablePeriodV1(
                start_date=document.start_date.isoformat(), end_date=document.end_date.isoformat()
            ).model_dump(mode="json"),
            "timezone": "Asia/Tokyo",
            "reference": document.url,
            "content_sha256": document.sha256,
            "data_version": document.sha256,
        }
        runtime_locator = {
            "root": "ir_raw_root",
            "task_id": ref.task_id,
            "path": relative_path,
            "sha256": document.sha256,
        }
        operator_locator = {"dependency": "interim_source", "path": operator_path, "sha256": document.sha256}
        provenance = {
            "metadata": metadata,
            "published_on": document.published_on.isoformat(),
            "runtime_receipt": receipt.model_dump(mode="json"),
            "runtime_locator": runtime_locator,
        }
        source_id = "source-ir-" + sha256(_bytes(provenance)).hexdigest()
        source = SourceMetadataEvidenceV1.model_validate_json(_bytes({"evidence_id": source_id, **metadata}))
        payloads[source_id] = provenance
        pdf_id = "pdf-" + document.sha256
        raw_payload = {
            "layer": "raw",
            "source_metadata_evidence_id": source_id,
            "payload_reference": ArtifactReferenceV1(
                artifact_type="raw_evidence_payload",
                artifact_id=pdf_id,
                schema_id="detailed-analysis.raw-evidence-payload",
                schema_version=1,
                sha256=document.sha256,
            ).model_dump(mode="json"),
            "content_sha256": document.sha256,
            "data_version": document.sha256,
        }
        raw_id = "raw-ir-" + sha256(_bytes(raw_payload)).hexdigest()
        raw_evidence = RawEvidenceV1.model_validate_json(_bytes({"evidence_id": raw_id, **raw_payload}))
        locators.setdefault(pdf_id, []).extend([runtime_locator, operator_locator])
        records.extend((source, raw_evidence))
        metrics = tuple(IrMetric.model_validate_json(_bytes(m)) for m in by_document[document.key]["metrics"])
        if sorted(m.metric for m in metrics) != sorted(METRICS):
            raise ValueError("interim_evidence_metric_scope_invalid")
        for metric in metrics:
            instant = metric.metric in {"assets", "equity"}
            if (
                metric.end_date != document.end_date
                or metric.start_date != (None if instant else document.start_date)
                or metric.period_basis != ("instant" if instant else "fiscal_year_to_date")
            ):
                raise ValueError("interim_evidence_metric_period_invalid")
            value = None
            if metric.status == "accepted":
                try:
                    number = Decimal(metric.value) if metric.value is not None else Decimal("NaN")
                except InvalidOperation as error:
                    raise ValueError("interim_evidence_accepted_value_invalid") from error
                if not number.is_finite():
                    raise ValueError("interim_evidence_accepted_value_invalid")
                value = metric.value
            period = ApplicablePeriodV1(
                start_date=metric.end_date.isoformat() if instant else document.start_date.isoformat(),
                end_date=metric.end_date.isoformat(),
            )
            missing_reason = None if value is not None else "required_input_not_accepted"
            payload = {
                "metric": metric.metric,
                "period": period.model_dump(mode="json"),
                "period_basis": metric.period_basis,
                "scope": metric.scope,
                "source_unit": metric.source_unit,
                "source_unit_multiplier": 1000000,
                "value_already_in_JPY": True,
                "value": value,
                "missing_reason": missing_reason,
                "unit": "JPY",
                "input_evidence_ids": [raw_id],
                "normalization_logic_version": 1,
                "operator_adoption": {
                    "dependency": "interim",
                    "values_sha256": values_hash,
                    "policy_sha256": policy_hash,
                    "fact": metric.model_dump(mode="json"),
                    "original_receipt": operator_receipt,
                    "receipt_locator": {
                        "dependency": "interim_source",
                        "path": operator_receipt_path,
                        "sha256": sha256(operator_source[operator_receipt_path]).hexdigest(),
                    },
                    "body_locator": operator_locator,
                },
                "runtime_content_match": document.sha256,
            }
            digest = sha256(_bytes(payload)).hexdigest()
            normalized = NormalizedEvidenceV1(
                evidence_id="normalized-ir-" + digest,
                layer="normalized",
                input_evidence_ids=(raw_id,),
                value=value,
                missing_reason=missing_reason,
                unit="JPY",
                content_sha256=digest,
                data_version=digest,
                normalization_logic_version=1,
            )
            payloads[normalized.evidence_id] = payload
            records.append(normalized)
    body = _bytes(
        {
            "version": 1,
            "kind": "internal-interim-p1-evidence-draft",
            "status": "pending",
            "analysis_ready": False,
            "task_id": acquisition.task_id,
            "records": [r.model_dump(mode="json") for r in sorted(records, key=lambda r: r.evidence_id)],
            "payloads": payloads,
            "artifact_locators": locators,
            "limitations": sorted(
                set(limitations)
                | {
                    "not_a_frozen_evidence_set",
                    "no_external_agent_transfer",
                    "interim_operator_evidence_not_task_bound",
                }
            ),
        }
    )
    return body, {"sha256": sha256(body).hexdigest(), "record_counts": dict(Counter(r.layer for r in records))}
