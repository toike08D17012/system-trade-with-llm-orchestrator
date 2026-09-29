"""P1 drafts preserve source provenance and metric-specific derived references."""

import copy
import json
from datetime import date
from decimal import localcontext
from hashlib import sha256
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.evidence import EvidenceRecordV1
from stock_research_llm_orchestrator.preparation.annual_calculations import calculate_annual
from stock_research_llm_orchestrator.preparation.annual_evidence import build_annual_evidence
from stock_research_llm_orchestrator.preparation.financial_acceptance import FinancialAdoptionPolicy, FinancialValue
from stock_research_llm_orchestrator.preparation.financial_comparative import AnnualPeriod, ComparativePeriodValues
from stock_research_llm_orchestrator.preparation.financial_mapping import METRICS
from stock_research_llm_orchestrator.preparation.financial_pair_acceptance import PairAdoptionPolicy
from stock_research_llm_orchestrator.sources.edinet.xbrl_document import EdinetXbrlDocumentAdapter
from stock_research_llm_orchestrator.sources.protocol import SourceParameter


def encoded(value: object) -> bytes:
    """Serialize fixture inputs with stable JSON bytes."""
    return json.dumps(value, sort_keys=True, indent=2).encode()


def fixture(pair: bool = False) -> tuple[dict[str, dict[str, bytes]], dict[str, tuple[ComparativePeriodValues, ...]]]:
    """Build source metadata without claiming a synthetic ZIP is an accepted filing."""
    policy = FinancialAdoptionPolicy.model_validate_json(
        Path("config/financial-mapping/7203-2026-approved.json").read_bytes()
    )
    if pair:
        pair_policy = PairAdoptionPolicy.model_validate_json(
            Path("config/financial-mapping/7203-2022-pair-approved.json").read_bytes()
        )
        policy = pair_policy.amended
    raw = b"synthetic raw archive"
    policy = policy.model_copy(update={"archive_sha256": sha256(raw).hexdigest()})
    source_key, values_key, acquisition_key = (
        ("pair", "pair_adoption", policy.document_id) if pair else ("financial", "comparative", "document")
    )
    approval = Path(
        "config/source-approvals/edinet/v5.yaml" if pair else "config/source-approvals/edinet/v2.yaml"
    ).read_bytes()
    intent = EdinetXbrlDocumentAdapter().build_intent(
        "document-retrieval",
        (
            SourceParameter(name="document_id", value=policy.document_id),
            SourceParameter(name="type", value="1"),
        ),
    )
    acquisition = {
        "key": acquisition_key,
        "publication": {
            "publication_id": "publication",
            "task_id": "task",
            "logical_request_id": "logical",
            "physical_attempt_id": "attempt",
            "source_id": "edinet",
            "operation": "document-retrieval",
            "content_sha256": policy.archive_sha256,
            "byte_count": len(raw),
            "media_type": "application/octet-stream",
            "encoding": "binary",
            "raw_schema_id": "edinet-xbrl-document-raw",
            "raw_schema_version": 1,
            "publication_generation": 1,
        },
        "source_intent": intent.model_dump(mode="json"),
        "retrieved_at": "2026-09-27T12:00:00Z",
        "acquisition_approval_sha256": sha256(approval).hexdigest(),
    }
    period = AnnualPeriod(start_date=date(2024, 4, 1), end_date=date(2025, 3, 31))
    values = ComparativePeriodValues(
        period=period,
        values=tuple(
            FinancialValue(
                metric=metric,
                status="accepted",
                value="100.000",
                concept=None,
                start_date=None if metric in {"assets", "equity"} else period.start_date,
                end_date=period.end_date,
                references=(),
                reasons=(),
            )
            for metric in METRICS
        ),
    )
    source = {f"raw/{acquisition_key}/body.bin": raw}
    if pair:
        source.update(
            {
                "approval.yaml": approval,
                "acquisitions.json": encoded([acquisition]),
                "retained-list/body.bin": encoded(
                    {
                        "results": [
                            {
                                "docID": policy.document_id,
                                "submitDateTime": "2023-06-30 14:13",
                                "periodStart": None,
                                "periodEnd": None,
                            }
                        ]
                    }
                ),
            }
        )
        adopted_policy = pair_policy.model_copy(update={"amended": policy}).model_dump_json().encode()
    else:
        source.update(
            {
                f"acquisition-approvals/{acquisition_key}.yaml": approval,
                "inputs.json": encoded(
                    {
                        "checked_at": "2026-09-28T00:00:00Z",
                        "survey_period": {"start_date": "2026-06-10", "end_date": "2026-06-10"},
                        "issuer": None,
                        "acquisitions": [acquisition],
                    }
                ),
                "manifest.json": encoded(
                    {
                        "task_id": "task",
                        "checked_at": "2026-09-28T00:00:00Z",
                        "generation": "generation",
                        "reasons": [],
                        "filings": [
                            {
                                "list_key": "list",
                                "archive_keys": [acquisition_key],
                                "reasons": [],
                                "document": {
                                    "sequence_number": 1,
                                    "document_id": policy.document_id,
                                    "edinet_code": "E02144",
                                    "security_code": "72030",
                                    "filer_name": "Fixture",
                                    "document_type": "120",
                                    "period_start": policy.start_date.isoformat(),
                                    "period_end": policy.end_date.isoformat(),
                                    "submitted_at": "2026-06-10 15:33",
                                    "description": "Annual",
                                    "parent_document_id": None,
                                    "withdrawal_status": "0",
                                    "xbrl_available": True,
                                },
                            }
                        ],
                        "observed_list_dates": [],
                        "missing_list_dates": [],
                        "annual_periods": [],
                        "interim_periods": [],
                        "price_fx_status": None,
                        "hashes": {},
                    }
                ),
            }
        )
        adopted_policy = policy.model_dump_json().encode()
    dependencies = {
        source_key: source,
        values_key: {"values.json": encoded([values.model_dump(mode="json")]), "policy.json": adopted_policy},
    }
    if not pair:
        dependencies["adoption"] = {"policy.json": adopted_policy}
    return dependencies, {values_key: (values,)}


def build(
    dependencies: dict[str, dict[str, bytes]],
    reports: dict[str, tuple[ComparativePeriodValues, ...]],
    required: tuple[AnnualPeriod, ...] | None = None,
) -> tuple[dict, dict[str, object], bytes]:
    """Use the real calculator before mapping its private rows to P1 models."""
    periods = required or tuple(
        sorted({r.period for report in reports.values() for r in report}, key=lambda p: p.start_date)
    )
    calculations, _ = calculate_annual(
        periods, reports, {key: sha256(dependencies[key]["values.json"]).hexdigest() for key in reports}
    )
    body, metadata = build_annual_evidence(
        "task",
        periods,
        reports,
        dependencies,
        calculations,
        ("operator_exported_acquisition_metadata_not_verified_against_runtime_database",),
    )
    return json.loads(body), metadata, body


@pytest.mark.parametrize("pair", [False, True])
def test_four_layers_bind_raw_acquisition_and_derived_payloads(pair: bool) -> None:
    """Real P1 models preserve original times, approval, ZIP bytes and input layers."""
    dependencies, reports = fixture(pair)
    draft, metadata, body = build(dependencies, reports)
    assert metadata["record_counts"] == {"source_metadata": 1, "raw": 1, "normalized": 6, "calculated": 4}
    assert metadata["sha256"] == sha256(body).hexdigest()
    adapter: TypeAdapter[EvidenceRecordV1] = TypeAdapter(EvidenceRecordV1)
    records = {r["evidence_id"]: r for r in draft["records"]}
    for record in records.values():
        adapter.validate_json(encoded(record))
        if record["layer"] in {"normalized", "calculated"}:
            assert record["content_sha256"] == sha256(encoded(draft["payloads"][record["evidence_id"]])).hexdigest()
            expected = "raw" if record["layer"] == "normalized" else "normalized"
            assert all(records[i]["layer"] == expected for i in record["input_evidence_ids"])
    source = next(r for r in records.values() if r["layer"] == "source_metadata")
    assert source["published_at"] == ("2023-06-30T14:13:00+09:00" if pair else "2026-06-10T15:33:00+09:00")
    assert source["retrieved_at"] == "2026-09-27T12:00:00Z"
    assert source["first_available_at"] is None and source["updated_at"] is None
    raw = next(r for r in records.values() if r["layer"] == "raw")
    locator = draft["artifact_locators"][raw["payload_reference"]["artifact_id"]][0]
    assert sha256(dependencies[locator["dependency"]][locator["path"]]).hexdigest() == raw["content_sha256"]
    assert raw["source_metadata_evidence_id"] == source["evidence_id"]
    assert "operator_exported_acquisition_metadata_not_verified_against_runtime_database" in draft["limitations"]
    assert draft["analysis_ready"] is False and "evidence_frozen_at" not in draft
    assert build(dependencies, reports)[2] == body


@pytest.mark.parametrize("distinct_acquisition", [False, True])
def test_equal_observations_keep_provenance_and_accepted_digits(distinct_acquisition: bool) -> None:
    """Duplicate raw identity is shared; all observations and precise values survive."""
    dependencies, reports = fixture()
    record = reports["comparative"][0]
    digits = "123456789012345678901234567890.123456789"
    record = record.model_copy(update={"values": tuple(v.model_copy(update={"value": digits}) for v in record.values)})
    reports["comparative"] = (record,)
    dependencies["comparative"]["values.json"] = encoded([record.model_dump(mode="json")])
    reports["additional_comparative"] = reports["comparative"]
    dependencies.update(
        {
            "additional_financial": dependencies["financial"],
            "additional_adoption": dependencies["adoption"],
            "additional_comparative": dependencies["comparative"],
        }
    )
    if distinct_acquisition:
        dependencies["additional_financial"] = copy.deepcopy(dependencies["financial"])
        additional_inputs = json.loads(dependencies["additional_financial"]["inputs.json"])
        additional_inputs["acquisitions"][0]["publication"]["physical_attempt_id"] = "other-attempt"
        dependencies["additional_financial"]["inputs.json"] = encoded(additional_inputs)
    with localcontext() as context:
        context.prec = 2
        draft, metadata, _ = build(dependencies, reports)
    counts = metadata["record_counts"]
    assert isinstance(counts, dict)
    assert counts["raw"] == (2 if distinct_acquisition else 1)
    for record in draft["records"]:
        if record["layer"] == "normalized":
            assert record["value"] == digits
            assert len(draft["payloads"][record["evidence_id"]]["observations"]) == 2
            assert len(record["input_evidence_ids"]) == (2 if distinct_acquisition else 1)


def test_missing_slots_and_outside_window_predecessor_are_distinct() -> None:
    """Missing normalized slots remain valid inputs without inventing a prior period."""
    dependencies, reports = fixture()
    record = reports["comparative"][0]
    record = record.model_copy(
        update={
            "values": tuple(
                v.model_copy(update={"status": "unaccepted", "value": None, "reasons": ("not_reviewed",)})
                for v in record.values
            )
        }
    )
    reports["comparative"] = (record,)
    dependencies["comparative"]["values.json"] = encoded([record.model_dump(mode="json")])
    following = AnnualPeriod(start_date=date(2025, 4, 1), end_date=date(2026, 3, 31))
    draft, _, _ = build(dependencies, reports, (record.period, following))
    normalized = [r for r in draft["records"] if r["layer"] == "normalized"]
    assert len(normalized) == 12 and all(r["value"] is None for r in normalized)
    calculated = [r for r in draft["records"] if r["layer"] == "calculated"]
    assert sum(r["missing_reason"] == "prior_period_outside_window" for r in calculated) == 1
    assert all(r["input_evidence_ids"] for r in calculated)
    payloads = [p for p in draft["payloads"].values() if "observations" in p]
    assert any(o["fact"]["reasons"] == ["not_reviewed"] for p in payloads for o in p["observations"])


@pytest.mark.parametrize("field", ["raw", "approval", "identity", "ambiguous", "published"])
def test_invalid_source_bindings_stop_draft(field: str) -> None:
    """Reject altered raw/approval, unmatched or repeated acquisition and future publication."""
    dependencies, reports = fixture()
    source = dependencies["financial"]
    if field == "raw":
        source["raw/document/body.bin"] += b"changed"
    elif field == "approval":
        source["acquisition-approvals/document.yaml"] += b"changed"
    elif field in {"identity", "ambiguous"}:
        inputs = json.loads(source["inputs.json"])
        if field == "identity":
            inputs["acquisitions"][0]["publication"]["content_sha256"] = "0" * 64
        else:
            inputs["acquisitions"] *= 2
        source["inputs.json"] = encoded(inputs)
    else:
        manifest = json.loads(source["manifest.json"])
        manifest["filings"][0]["document"]["submitted_at"] = "2026-09-28 15:00"
        source["manifest.json"] = encoded(manifest)
    with pytest.raises(ValueError):
        build(dependencies, reports)
