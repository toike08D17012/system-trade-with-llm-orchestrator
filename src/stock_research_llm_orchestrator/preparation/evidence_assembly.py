"""Assemble replayed private P1 drafts without asserting freeze or freshness."""

import json
from collections import Counter
from collections.abc import Mapping
from hashlib import sha256

from pydantic import TypeAdapter

from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.evidence import (
    CalculatedEvidenceV1,
    EvidenceRecordV1,
    NormalizedEvidenceV1,
    RawEvidenceV1,
    SourceMetadataEvidenceV1,
)


DRAFT_CATEGORIES = ("annual", "interim", "price-fx", "narrative")


def _bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, indent=2).encode()


def build_evidence_assembly(drafts: Mapping[str, bytes]) -> tuple[bytes, dict[str, object]]:
    """Retain original provenance and reject conflicting identities or broken layers."""
    if set(drafts) != set(DRAFT_CATEGORIES):
        raise ValueError("evidence_assembly_draft_set_invalid")
    adapter: TypeAdapter[EvidenceRecordV1] = TypeAdapter(EvidenceRecordV1)
    records: dict[str, EvidenceRecordV1] = {}
    payloads: dict[str, object] = {}
    provenance: dict[str, object] = {}
    limitations: set[str] = set()
    task_id = None
    for category in DRAFT_CATEGORIES:
        draft = json.loads(drafts[category])
        if (
            draft["kind"] != f"internal-{category}-p1-evidence-draft"
            or draft["version"] != 1
            or draft["status"] != "pending"
            or draft["analysis_ready"] is not False
        ):
            raise ValueError("evidence_assembly_draft_state_invalid")
        if task_id is None:
            task_id = draft["task_id"]
        if draft["task_id"] != task_id:
            raise ValueError("evidence_assembly_task_mismatch")
        local_ids = set()
        for data in draft["records"]:
            record = adapter.validate_json(_bytes(data))
            key = record.evidence_id
            if key in local_ids:
                raise ValueError("evidence_assembly_duplicate_within_draft")
            local_ids.add(key)
            if key in records and _bytes(records[key].model_dump(mode="json")) != _bytes(
                record.model_dump(mode="json")
            ):
                raise ValueError("evidence_assembly_record_conflict")
            if key in records and (
                (key in payloads) != (key in draft["payloads"])
                or _bytes(payloads.get(key)) != _bytes(draft["payloads"].get(key))
            ):
                raise ValueError("evidence_assembly_payload_conflict")
            records[key] = record
        if set(draft["payloads"]) - local_ids:
            raise ValueError("evidence_assembly_unknown_provenance_id")
        for key, payload in draft["payloads"].items():
            payloads[key] = payload
        provenance[category] = {
            "draft_sha256": sha256(drafts[category]).hexdigest(),
            "record_ids": sorted(local_ids),
            "artifact_locators": draft["artifact_locators"],
            "limitations": draft["limitations"],
            **({"context": draft["context"], "context_sha256": draft["context_sha256"]} if "context" in draft else {}),
        }
        limitations.update(draft["limitations"])
    for record in records.values():
        if isinstance(record, RawEvidenceV1):
            if not isinstance(records.get(record.source_metadata_evidence_id), SourceMetadataEvidenceV1):
                raise ValueError("evidence_assembly_source_reference_invalid")
        elif isinstance(record, (NormalizedEvidenceV1, CalculatedEvidenceV1)):
            expected = RawEvidenceV1 if isinstance(record, NormalizedEvidenceV1) else NormalizedEvidenceV1
            if any(not isinstance(records.get(key), expected) for key in record.input_evidence_ids):
                raise ValueError("evidence_assembly_input_reference_invalid")
    body = _bytes(
        {
            "kind": "internal-assembled-p1-evidence-draft",
            "version": 1,
            "task_id": task_id,
            "status": "pending",
            "analysis_ready": False,
            "records": [records[key].model_dump(mode="json") for key in sorted(records)],
            "payloads": payloads,
            "draft_provenance": provenance,
            "limitations": sorted(
                limitations | {"unfrozen_assembly", "disclosure_coverage_and_final_freshness_unresolved"}
            ),
        }
    )
    return body, {
        "sha256": sha256(body).hexdigest(),
        "record_counts": dict(sorted(Counter(record.layer for record in records.values()).items())),
    }
