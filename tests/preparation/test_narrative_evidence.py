"""Qualitative P1 records retain selected judgments and original acquisition identity."""

import json
from hashlib import sha256
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.evidence import EvidenceRecordV1
from stock_research_llm_orchestrator.preparation.interim_ir_runtime import IrRuntimeManifest, IrRuntimeReceipt
from stock_research_llm_orchestrator.preparation.narrative_evidence import build_narrative_evidence
from stock_research_llm_orchestrator.requests.production import CommittedRawReference


def encoded(value: object) -> bytes:
    """Serialize private fixtures in the same canonical format as draft payloads."""
    return json.dumps(value, sort_keys=True, indent=2).encode()


def fixture(tmp_path: Path) -> tuple[dict, dict, bytes, dict[str, bytes], dict[str, bytes], Path]:
    """Simulate bound reviews and an already replayed annual correction source."""
    body = b"synthetic PDF"
    digest = sha256(body).hexdigest()
    raw = tmp_path / "task/acquisitions/logical/physical"
    raw.mkdir(parents=True)
    (raw / "body.bin").write_bytes(body)
    ref = CommittedRawReference(
        publication_id="publication",
        task_id="task",
        logical_request_id="logical",
        physical_attempt_id="physical",
        relative_path="acquisitions/logical/physical",
        content_sha256=digest,
        byte_count=len(body),
        raw_schema_id="toyota-ir-pdf-raw",
        raw_schema_version=1,
        publication_generation=1,
    )
    approval = Path("config/source-approvals/toyota-ir/v2.yaml").read_bytes()
    acquisition = IrRuntimeManifest(
        kind="toyota-disclosure-runtime-candidates",
        task_id="task",
        receipts=(IrRuntimeReceipt(key="doc", received_at="2026-09-28T12:00:00Z", raw_reference=ref),),
        hashes={"approval.yaml": sha256(approval).hexdigest()},
    )
    entry = {
        "key": "doc",
        "content_sha256": digest,
        "published_on": "2026-09-03",
        "extraction_sha256": "e" * 64,
        "selected_pages": [1],
        "facts": ["approved selected assertion"],
    }
    disclosure = {
        "review_sha256": "f" * 64,
        "acquisition": acquisition.model_dump(mode="json"),
        "review": {
            "entries": [entry],
            "reviewed_at": "2026-09-29T00:00:00Z",
            "reviewed_by": "owner",
            "review_mode": "selected_sections",
            "relations": ["unresolved_relation"],
            "unresolved_external_references": ["not_acquired"],
            "complete_disclosure_coverage": False,
            "financial_values_adopted": False,
            "limitations": ["selected_only"],
        },
    }
    correction = {
        "task_id": "task",
        "archive_sha256": "a" * 64,
        "document_id": "S100RAR0",
        "member": "selected.html",
        "member_sha256": "b" * 64,
        "reviewed_at": "2026-09-28T00:00:00Z",
        "review_mode": "selected_sections",
        "correction_kind": "attachments_omitted",
        "financial_restatement_described_in_selected_sections": False,
        "limitations": ["correction_selected_only"],
    }
    correction_body = encoded(correction)
    correction["review_sha256"] = sha256(correction_body).hexdigest()
    source = {
        "evidence_id": "annual-source",
        "layer": "source_metadata",
        "source_id": "edinet",
        "source_approval_reference": {
            "artifact_type": "source_approval",
            "artifact_id": "edinet-approval",
            "schema_id": "detailed-analysis.source-approval",
            "schema_version": 1,
            "sha256": "c" * 64,
        },
        "provider": "FSA",
        "service": "EDINET",
        "dataset": "Annual filing",
        "published_at": "2023-06-30T14:13:00+09:00",
        "first_available_at": None,
        "updated_at": None,
        "retrieved_at": "2026-09-27T13:25:00Z",
        "applicable_period": {"start_date": "2022-04-01", "end_date": "2023-03-31"},
        "timezone": "Asia/Tokyo",
        "reference": "EDINET:S100RAR0",
        "content_sha256": "a" * 64,
        "data_version": "a" * 64,
    }
    raw_record = {
        "evidence_id": "annual-raw",
        "layer": "raw",
        "source_metadata_evidence_id": "annual-source",
        "payload_reference": {
            "artifact_type": "raw_evidence_payload",
            "artifact_id": "amended-zip",
            "schema_id": "detailed-analysis.raw-evidence-payload",
            "schema_version": 1,
            "sha256": "a" * 64,
        },
        "content_sha256": "a" * 64,
        "data_version": "a" * 64,
    }
    annual = encoded(
        {
            "task_id": "task",
            "records": [source, raw_record],
            "payloads": {
                "annual-source": {
                    "acquisition": {"source_intent": {"parameters": [{"name": "document_id", "value": "S100RAR0"}]}},
                    "metadata": source,
                }
            },
            "artifact_locators": {
                "edinet-approval": [{"dependency": "pair", "path": "approval.yaml", "sha256": "c" * 64}],
                "amended-zip": [{"dependency": "pair", "path": "raw/S100RAR0/body.bin", "sha256": "a" * 64}],
            },
        }
    )
    files = {
        "approval.yaml": approval,
        "policy.json": encoded(
            {"documents": [{"key": "doc", "published_on": "2026-09-03", "url": "https://example.invalid/doc.pdf"}]}
        ),
    }
    return disclosure, correction, annual, files, {"narrative-review.json": correction_body}, tmp_path


def test_p1_selected_judgments_times_ids_and_hashes(tmp_path: Path) -> None:
    """Source IDs are reused and review time never replaces retrieval time."""
    args = fixture(tmp_path)
    body, metadata = build_narrative_evidence(*args, ("existing_limit",))
    draft = json.loads(body)
    assert metadata["record_counts"] == {"source_metadata": 2, "raw": 2, "normalized": 2}
    adapter: TypeAdapter[EvidenceRecordV1] = TypeAdapter(EvidenceRecordV1)
    records = {r["evidence_id"]: r for r in draft["records"]}
    for r in records.values():
        adapter.validate_json(encoded(r))
        if r["layer"] == "normalized":
            assert r["unit"] == "qualitative_review" and r["missing_reason"] is None
            p = draft["payloads"][r["evidence_id"]]
            assert r["content_sha256"] == sha256(encoded(p)).hexdigest()
            assert p["semantic_review_reperformed"] is False
            assert all(records[i]["layer"] == "raw" for i in r["input_evidence_ids"])
        elif r["layer"] == "raw":
            assert records[r["source_metadata_evidence_id"]]["layer"] == "source_metadata"
    annual = json.loads(args[2])
    assert records["annual-source"] == annual["records"][0] and records["annual-raw"] == annual["records"][1]
    assert draft["payloads"]["annual-source"] == annual["payloads"]["annual-source"]
    assert draft["artifact_locators"]["amended-zip"] == annual["artifact_locators"]["amended-zip"]
    latest = next(r for r in records.values() if r["layer"] == "source_metadata" and r["source_id"] == "toyota-ir")
    assert latest["published_at"] is None and latest["retrieved_at"] == "2026-09-28T12:00:00Z"
    assert draft["context"]["selected_review"]["unresolved_external_references"] == ["not_acquired"]
    assert draft["context"]["selected_review"]["complete_disclosure_coverage"] is False
    assert draft["context"]["selected_review"]["financial_values_adopted"] is False
    assert draft["analysis_ready"] is False and "evidence_frozen_at" not in draft
    assert build_narrative_evidence(*args, ("existing_limit",))[0] == body


@pytest.mark.parametrize("mode", ["raw", "approval", "correction_hash", "wrong_document", "ambiguous", "task", "keys"])
def test_unbound_narrative_inputs_rejected(tmp_path: Path, mode: str) -> None:
    """Reject source, identity and task mismatches without new judgments."""
    disclosure, correction, annual, files, reviews, root = fixture(tmp_path)
    if mode == "raw":
        (root / "task/acquisitions/logical/physical/body.bin").write_bytes(b"changed")
    elif mode == "approval":
        files["approval.yaml"] += b"changed"
    elif mode == "correction_hash":
        reviews["narrative-review.json"] += b"changed"
    elif mode == "wrong_document":
        correction["document_id"] = "other-document"
    elif mode == "keys":
        disclosure["review"]["entries"][0]["key"] = "other-doc"
    else:
        a = json.loads(annual)
        if mode == "task":
            a["task_id"] = "other-task"
        else:
            a["records"].append({**a["records"][1], "evidence_id": "duplicate-raw"})
        annual = encoded(a)
    with pytest.raises(ValueError, match="narrative_evidence_"):
        build_narrative_evidence(disclosure, correction, annual, files, reviews, root, ())
