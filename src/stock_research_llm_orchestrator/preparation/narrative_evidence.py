"""Private P1 narrative records project fixed reviews without new semantic judgment."""

import json
from collections import Counter
from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path

import yaml
from pydantic import TypeAdapter

from stock_research_llm_orchestrator.contracts.base import ArtifactReferenceV1
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.evidence import (
    ApplicablePeriodV1,
    EvidenceRecordV1,
    NormalizedEvidenceV1,
    RawEvidenceV1,
    SourceMetadataEvidenceV1,
)
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.policies import SourceApprovalV1
from stock_research_llm_orchestrator.preparation.interim_ir_runtime import IrRuntimeManifest
from stock_research_llm_orchestrator.preparation.storage import read_bundle


def _bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, indent=2).encode()


def _normalized(raw_id: str, value: object, provenance: object, context_hash: str) -> tuple[NormalizedEvidenceV1, dict]:
    """Bind one adopted selected-section judgment, never a newly inferred claim."""
    payload = {
        "value": value,
        "missing_reason": None,
        "unit": "qualitative_review",
        "input_evidence_ids": [raw_id],
        "normalization_logic_version": 1,
        "provenance": provenance,
        "context_sha256": context_hash,
        "semantic_review_reperformed": False,
    }
    digest = sha256(_bytes(payload)).hexdigest()
    record = NormalizedEvidenceV1.model_validate_json(
        _bytes(
            {
                "evidence_id": "normalized-narrative-" + digest,
                "layer": "normalized",
                "value": value,
                "missing_reason": None,
                "unit": "qualitative_review",
                "input_evidence_ids": [raw_id],
                "normalization_logic_version": 1,
                "content_sha256": digest,
                "data_version": digest,
            }
        )
    )
    return record, payload


def build_narrative_evidence(
    disclosure: Mapping[str, object],
    correction: Mapping[str, object],
    annual_body: bytes,
    acquisition_files: Mapping[str, bytes],
    correction_files: Mapping[str, bytes],
    raw_root: Path,
    limitations: tuple[str, ...],
) -> tuple[bytes, dict[str, object]]:
    """Map already bound supplements and replayed annual provenance to an unfrozen draft."""
    review = json.loads(_bytes(disclosure["review"]))
    acquisition = IrRuntimeManifest.model_validate_json(_bytes(disclosure["acquisition"]))
    annual = json.loads(annual_body)
    if (
        acquisition.kind != "toyota-disclosure-runtime-candidates"
        or annual["task_id"] != acquisition.task_id
        or correction["task_id"] != acquisition.task_id
        or len(review["entries"]) != len(acquisition.receipts)
        or tuple(e["key"] for e in review["entries"]) != tuple(r.key for r in acquisition.receipts)
        or len({e["key"] for e in review["entries"]}) != len(review["entries"])
    ):
        raise ValueError("narrative_evidence_scope_invalid")
    correction_body = correction_files["narrative-review.json"]
    if sha256(correction_body).hexdigest() != correction["review_sha256"]:
        raise ValueError("narrative_evidence_correction_hash_invalid")
    context = {
        "selected_review": {k: v for k, v in review.items() if k != "entries"},
        "selected_review_sha256": disclosure["review_sha256"],
        "correction_review": json.loads(correction_body),
        "correction_binding": dict(correction),
        "semantic_review_reperformed": False,
        "analysis_ready": False,
    }
    context_hash = sha256(_bytes(context)).hexdigest()
    approval_body = acquisition_files["approval.yaml"]
    approval_hash = sha256(approval_body).hexdigest()
    if acquisition.hashes["approval.yaml"] != approval_hash:
        raise ValueError("narrative_evidence_approval_hash_invalid")
    approval = SourceApprovalV1.model_validate(yaml.safe_load(approval_body))
    documents = json.loads(acquisition_files["policy.json"])["documents"]
    by_key = {d["key"]: d for d in documents}
    if len(by_key) != len(documents) or set(by_key) != {e["key"] for e in review["entries"]}:
        raise ValueError("narrative_evidence_policy_scope_invalid")
    approval_ref = ArtifactReferenceV1(
        artifact_type="source_approval",
        artifact_id=approval.artifact_id,
        schema_id=approval.schema_id,
        schema_version=approval.schema_version,
        sha256=approval_hash,
    )
    records: dict[str, EvidenceRecordV1] = {}
    payloads: dict[str, object] = {}
    locators = {
        approval.artifact_id: [{"dependency": "disclosure_runtime", "path": "approval.yaml", "sha256": approval_hash}]
    }
    for entry, receipt in zip(review["entries"], acquisition.receipts, strict=True):
        document = by_key[entry["key"]]
        ref = receipt.raw_reference
        body = read_bundle(raw_root / ref.task_id / ref.relative_path)["body.bin"]
        if (
            ref.task_id != acquisition.task_id
            or ref.content_sha256 != entry["content_sha256"]
            or sha256(body).hexdigest() != ref.content_sha256
            or entry["published_on"] != document["published_on"]
        ):
            raise ValueError("narrative_evidence_raw_binding_invalid")
        day = entry["published_on"]
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
            "applicable_period": ApplicablePeriodV1(start_date=day, end_date=day).model_dump(mode="json"),
            "timezone": "Asia/Tokyo",
            "reference": document["url"],
            "content_sha256": ref.content_sha256,
            "data_version": ref.content_sha256,
        }
        locator = {
            "root": "disclosure_raw_root",
            "task_id": ref.task_id,
            "path": f"{ref.task_id}/{ref.relative_path}/body.bin",
            "sha256": ref.content_sha256,
        }
        provenance = {
            "metadata": metadata,
            "published_on": day,
            "period_basis": "disclosure_publication_date",
            "receipt": receipt.model_dump(mode="json"),
            "locator": locator,
        }
        source_id = "source-narrative-" + sha256(_bytes(provenance)).hexdigest()
        source = SourceMetadataEvidenceV1.model_validate_json(_bytes({"evidence_id": source_id, **metadata}))
        payloads[source_id] = provenance
        pdf_id = "pdf-" + ref.content_sha256
        raw_payload = {
            "layer": "raw",
            "source_metadata_evidence_id": source_id,
            "payload_reference": ArtifactReferenceV1(
                artifact_type="raw_evidence_payload",
                artifact_id=pdf_id,
                schema_id="detailed-analysis.raw-evidence-payload",
                schema_version=1,
                sha256=ref.content_sha256,
            ).model_dump(mode="json"),
            "content_sha256": ref.content_sha256,
            "data_version": ref.content_sha256,
        }
        raw_id = "raw-narrative-" + sha256(_bytes(raw_payload)).hexdigest()
        raw = RawEvidenceV1.model_validate_json(_bytes({"evidence_id": raw_id, **raw_payload}))
        records[source_id] = source
        records[raw_id] = raw
        locators.setdefault(pdf_id, []).append(locator)
        normalized, payload = _normalized(
            raw_id,
            entry["facts"],
            {
                "entry": entry,
                "review_sha256": disclosure["review_sha256"],
                "reviewed_at": review["reviewed_at"],
                "reviewed_by": review["reviewed_by"],
                "review_mode": review["review_mode"],
            },
            context_hash,
        )
        records[normalized.evidence_id] = normalized
        payloads[normalized.evidence_id] = payload
    adapter: TypeAdapter[EvidenceRecordV1] = TypeAdapter(EvidenceRecordV1)
    annual_records = {r["evidence_id"]: r for r in annual["records"]}
    selected = []
    for raw in annual_records.values():
        if raw["layer"] != "raw" or raw["content_sha256"] != correction["archive_sha256"]:
            continue
        source_id = raw["source_metadata_evidence_id"]
        provenance = annual["payloads"][source_id]
        parameters = provenance["acquisition"]["source_intent"]["parameters"]
        if {p["name"]: p["value"] for p in parameters}.get("document_id") == correction["document_id"]:
            selected.append(raw)
    if len(selected) != 1:
        raise ValueError("narrative_evidence_correction_source_ambiguous")
    correction_raw = selected[0]
    correction_source = annual_records[correction_raw["source_metadata_evidence_id"]]
    for item in (correction_source, correction_raw):
        record = adapter.validate_json(_bytes(item))
        records[record.evidence_id] = record
    payloads[correction_source["evidence_id"]] = annual["payloads"][correction_source["evidence_id"]]
    for artifact_id in (
        correction_raw["payload_reference"]["artifact_id"],
        correction_source["source_approval_reference"]["artifact_id"],
    ):
        locators[artifact_id] = annual["artifact_locators"][artifact_id]
    normalized, payload = _normalized(
        correction_raw["evidence_id"],
        {
            "correction_kind": correction["correction_kind"],
            "financial_restatement_described_in_selected_sections": correction[
                "financial_restatement_described_in_selected_sections"
            ],
        },
        dict(correction),
        context_hash,
    )
    records[normalized.evidence_id] = normalized
    payloads[normalized.evidence_id] = payload
    for record in records.values():
        if isinstance(record, RawEvidenceV1) and not isinstance(
            records.get(record.source_metadata_evidence_id), SourceMetadataEvidenceV1
        ):
            raise ValueError("narrative_evidence_source_reference_invalid")
        if isinstance(record, NormalizedEvidenceV1) and any(
            not isinstance(records.get(i), RawEvidenceV1) for i in record.input_evidence_ids
        ):
            raise ValueError("narrative_evidence_input_layer_invalid")
    body = _bytes(
        {
            "version": 1,
            "kind": "internal-narrative-p1-evidence-draft",
            "status": "pending",
            "analysis_ready": False,
            "task_id": acquisition.task_id,
            "context": context,
            "context_sha256": context_hash,
            "records": [r.model_dump(mode="json") for _, r in sorted(records.items())],
            "payloads": payloads,
            "artifact_locators": locators,
            "limitations": sorted(
                set(limitations)
                | set(review["limitations"])
                | set(json.loads(_bytes(correction["limitations"])))
                | {
                    "not_a_frozen_evidence_set",
                    "not_transferred_during_preparation",
                    "selected_narrative_not_complete_disclosure_coverage",
                }
            ),
        }
    )
    return body, {"sha256": sha256(body).hexdigest(), "record_counts": dict(Counter(r.layer for r in records.values()))}
