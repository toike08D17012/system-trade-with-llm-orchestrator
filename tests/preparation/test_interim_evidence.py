"""IR P1 drafts preserve cumulative/instant facts and separate acquisition histories."""

import json
from hashlib import sha256
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.evidence import EvidenceRecordV1
from stock_research_llm_orchestrator.preparation.financial_mapping import METRICS
from stock_research_llm_orchestrator.preparation.interim_evidence import build_interim_evidence
from stock_research_llm_orchestrator.preparation.interim_ir import InterimPolicy
from stock_research_llm_orchestrator.preparation.interim_ir_runtime import IrRuntimeManifest, IrRuntimeReceipt
from stock_research_llm_orchestrator.requests.production import CommittedRawReference
from stock_research_llm_orchestrator.sources.toyota_ir_pdf import IrMetric


def encoded(value: object) -> bytes:
    """Use production-style canonical serialization for expected hash checks."""
    return json.dumps(value, sort_keys=True, indent=2).encode()


def inputs(
    tmp_path: Path,
) -> tuple[InterimPolicy, dict[str, bytes], dict[str, bytes], IrRuntimeManifest, dict[str, bytes], Path]:
    """Represent already replayed adoption and independently verified runtime bytes."""
    policy = InterimPolicy.model_validate_json(
        Path("config/financial-mapping/7203-interim-ir-approved.json").read_bytes()
    )
    approval = Path("config/source-approvals/toyota-ir/v1.yaml").read_bytes()
    runtime_files = {"approval.yaml": approval}
    documents = []
    values = []
    source = {}
    receipts = []
    for index, document in enumerate(policy.documents):
        body = f"%PDF-synthetic-{index}".encode()
        digest = sha256(body).hexdigest()
        document = document.model_copy(update={"sha256": digest})
        documents.append(document)
        source[f"{document.key}/body.pdf"] = body
        source[f"{document.key}/receipt.json"] = encoded(
            {
                "retrieved_at": "2026-09-27T12:00:00Z",
                "sha256": digest,
                "url": document.url,
                "status": 200,
                "bytes": len(body),
            }
        )
        metrics = tuple(
            IrMetric(
                metric=metric,
                status="accepted",
                value="-3000000" if metric == "operating_cash_flow" else "100000000",
                start_date=None if metric in {"assets", "equity"} else document.start_date,
                end_date=document.end_date,
                period_basis="instant" if metric in {"assets", "equity"} else "fiscal_year_to_date",
                references=((1, 2, 3),),
                reasons=(),
            )
            for metric in METRICS
        )
        values.append({"document": document.key, "metrics": [m.model_dump(mode="json") for m in metrics]})
        ref = CommittedRawReference(
            publication_id=f"pub-{index}",
            task_id="task",
            logical_request_id=f"logical-{index}",
            physical_attempt_id=f"attempt-{index}",
            relative_path=f"acquisitions/group/raw-{index}",
            content_sha256=digest,
            byte_count=len(body),
            raw_schema_id="toyota-ir-pdf-raw",
            raw_schema_version=1,
            publication_generation=1,
        )
        directory = tmp_path / ref.task_id / ref.relative_path
        directory.mkdir(parents=True)
        (directory / "body.bin").write_bytes(body)
        receipts.append(IrRuntimeReceipt(key=document.key, received_at="2026-09-28T12:00:00Z", raw_reference=ref))
    policy = policy.model_copy(update={"documents": tuple(documents)})
    adoption = {"policy.json": policy.model_dump_json().encode(), "values.json": encoded(values)}
    acquisition = IrRuntimeManifest(
        task_id="task", receipts=tuple(receipts), hashes={"approval.yaml": sha256(approval).hexdigest()}
    )
    return policy, adoption, source, acquisition, runtime_files, tmp_path


def build(
    args: tuple[InterimPolicy, dict[str, bytes], dict[str, bytes], IrRuntimeManifest, dict[str, bytes], Path],
) -> tuple[dict, dict[str, object], bytes]:
    """Parse the private result without printing source values."""
    body, metadata = build_interim_evidence(*args, ("operator_acquisition_metadata_not_runtime_verified",))
    return json.loads(body), metadata, body


def test_p1_layers_periods_units_locators_and_operator_history(tmp_path: Path) -> None:
    """Check no double multiplication, invented publication time or provenance replacement."""
    args = inputs(tmp_path)
    policy, adoption, source, acquisition, runtime_files, raw_root = args
    draft, metadata, body = build(args)
    assert metadata["record_counts"] == {"source_metadata": 8, "raw": 8, "normalized": 48}
    assert metadata["sha256"] == sha256(body).hexdigest()
    records = {r["evidence_id"]: r for r in draft["records"]}
    adapter: TypeAdapter[EvidenceRecordV1] = TypeAdapter(EvidenceRecordV1)
    for record in records.values():
        adapter.validate_json(encoded(record))
        if record["layer"] == "source_metadata":
            assert (
                record["published_at"] is None and record["updated_at"] is None and record["first_available_at"] is None
            )
            assert record["retrieved_at"] == "2026-09-28T12:00:00Z"
            assert draft["payloads"][record["evidence_id"]]["published_on"]
        elif record["layer"] == "raw":
            assert records[record["source_metadata_evidence_id"]]["layer"] == "source_metadata"
            locators = draft["artifact_locators"][record["payload_reference"]["artifact_id"]]
            assert len(locators) == 2
            runtime_locator, operator_locator = locators
            assert runtime_locator["root"] == "ir_raw_root" and runtime_locator["task_id"] == "task"
            assert sha256((raw_root / runtime_locator["path"]).read_bytes()).hexdigest() == record["content_sha256"]
            assert sha256(source[operator_locator["path"]]).hexdigest() == record["content_sha256"]
        else:
            payload = draft["payloads"][record["evidence_id"]]
            assert record["content_sha256"] == sha256(encoded(payload)).hexdigest()
            assert record["unit"] == "JPY" and record["value"] in {"100000000", "-3000000"}
            assert payload["source_unit"] == "JPY_millions" and payload["source_unit_multiplier"] == 1000000
            assert payload["value_already_in_JPY"] is True
            assert all(records[i]["layer"] == "raw" for i in record["input_evidence_ids"])
            if payload["metric"] in {"assets", "equity"}:
                assert payload["period_basis"] == "instant"
                assert payload["period"]["start_date"] == payload["period"]["end_date"]
            else:
                assert payload["period_basis"] == "fiscal_year_to_date"
                assert payload["period"]["start_date"].endswith("04-01")
            history = payload["operator_adoption"]
            assert history["original_receipt"]["retrieved_at"] == "2026-09-27T12:00:00Z"
            assert history["fact"]["references"] == [[1, 2, 3]]
            assert history["values_sha256"] == sha256(adoption["values.json"]).hexdigest()
    approval_locator = draft["artifact_locators"]["toyota-ir-source-approval-v1"][0]
    assert (
        approval_locator["dependency"] == "interim_runtime"
        and approval_locator["sha256"] == sha256(runtime_files["approval.yaml"]).hexdigest()
    )
    assert "operator_acquisition_metadata_not_runtime_verified" in draft["limitations"]
    assert "interim_operator_evidence_not_task_bound" in draft["limitations"]
    assert draft["analysis_ready"] is False and "evidence_frozen_at" not in draft
    assert build(args)[2] == body


def test_missing_metric_retains_null_reason_and_fact_history(tmp_path: Path) -> None:
    """An unaccepted stored number is not promoted to a normalized value."""
    args = inputs(tmp_path)
    values = json.loads(args[1]["values.json"])
    values[0]["metrics"][0].update(status="unaccepted", reasons=["missing_or_unsupported_row"])
    args[1]["values.json"] = encoded(values)
    draft, _, _ = build(args)
    missing = [r for r in draft["records"] if r["layer"] == "normalized" and r["value"] is None]
    assert len(missing) == 1 and missing[0]["missing_reason"] == "required_input_not_accepted"
    fact = draft["payloads"][missing[0]["evidence_id"]]["operator_adoption"]["fact"]
    assert fact["reasons"] == ["missing_or_unsupported_row"] and fact["status"] == "unaccepted"


@pytest.mark.parametrize(
    "change",
    [
        "runtime_raw",
        "operator_raw",
        "approval",
        "duplicate_receipt",
        "duplicate_document",
        "kind",
        "metric_keys",
        "period",
        "value",
    ],
)
def test_mismatched_inputs_stop_draft(tmp_path: Path, change: str) -> None:
    """Reject content changes, ambiguous documents and invalid normalized slots."""
    policy, adoption, source, acquisition, runtime_files, root = inputs(tmp_path)
    if change == "runtime_raw":
        ref = acquisition.receipts[0].raw_reference
        (root / ref.task_id / ref.relative_path / "body.bin").write_bytes(b"changed")
    elif change == "operator_raw":
        source[f"{policy.documents[0].key}/body.pdf"] = b"changed"
    elif change == "approval":
        runtime_files["approval.yaml"] += b"changed"
    elif change == "duplicate_receipt":
        acquisition = acquisition.model_copy(
            update={"receipts": acquisition.receipts[:-1] + (acquisition.receipts[0],)}
        )
    elif change == "kind":
        acquisition = acquisition.model_copy(update={"kind": "toyota-disclosure-runtime-candidates"})
    else:
        values = json.loads(adoption["values.json"])
        if change == "duplicate_document":
            values[-1] = values[0]
        elif change == "metric_keys":
            values[0]["metrics"][0] = values[0]["metrics"][1]
        elif change == "period":
            values[0]["metrics"][0]["end_date"] = "2020-03-31"
        else:
            values[0]["metrics"][0]["value"] = "NaN"
        adoption["values.json"] = encoded(values)
    with pytest.raises(ValueError, match="interim_evidence_"):
        build((policy, adoption, source, acquisition, runtime_files, root))


def test_runtime_attempt_identity_changes_source_and_derived_ids(tmp_path: Path) -> None:
    """Equal PDF bytes do not collapse a distinct runtime attempt into one history."""
    policy, adoption, source, acquisition, runtime_files, root = inputs(tmp_path)
    before, _, _ = build((policy, adoption, source, acquisition, runtime_files, root))
    first = acquisition.receipts[0]
    ref = first.raw_reference.model_copy(update={"physical_attempt_id": "other-attempt"})
    changed = first.model_copy(update={"raw_reference": ref})
    acquisition = acquisition.model_copy(update={"receipts": (changed,) + acquisition.receipts[1:]})
    after, _, _ = build((policy, adoption, source, acquisition, runtime_files, root))
    assert {r["evidence_id"] for r in before["records"]} != {r["evidence_id"] for r in after["records"]}
    assert before["artifact_locators"] == after["artifact_locators"]
