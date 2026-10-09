"""Freeze a validated market preparation without claiming analysis readiness."""

import argparse
import json
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from stock_research_llm_orchestrator.contracts.base import ArtifactReferenceV1
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.evidence import (
    EvidenceSetV1,
    RawEvidenceV1,
    SourceMetadataEvidenceV1,
)
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.policies import SourceApprovalV1
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.runtime import ExecutionManifestV1
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.task import DetailedAnalysisTaskV1
from stock_research_llm_orchestrator.preparation.market_evidence import (
    JpxSnapshotProvenance,
    MarketPreparationIndex,
    validate_market_preparation,
)
from stock_research_llm_orchestrator.preparation.storage import publish_preparation, read_bundle


def _digest(body: bytes) -> str:
    return sha256(body).hexdigest()


def _reference(artifact_type: str, artifact_id: str, schema_id: str, body: bytes) -> ArtifactReferenceV1:
    return ArtifactReferenceV1(
        artifact_type=artifact_type,
        artifact_id=artifact_id,
        schema_id=schema_id,
        schema_version=1,
        sha256=_digest(body),
    )


def _serialize(value: EvidenceSetV1 | ExecutionManifestV1) -> bytes:
    return value.model_dump_json(indent=2).encode() + b"\n"


def build_frozen_market(
    market: Mapping[str, bytes], approvals: Mapping[str, bytes], frozen_at: str
) -> dict[str, bytes]:
    """Bind exact validated source bytes and approval snapshots to an initial evidence set."""
    validate_market_preparation(market)
    if set(approvals) != {"jpx", "yfinance"}:
        raise ValueError("freeze_approval_inventory_invalid")
    frozen = datetime.fromisoformat(frozen_at.replace("Z", "+00:00"))
    if frozen.utcoffset() is None:
        raise ValueError("freeze_time_requires_offset")
    task = DetailedAnalysisTaskV1.model_validate_json(market["task.json"])
    index = MarketPreparationIndex.model_validate_json(market["index.json"])
    jpx = JpxSnapshotProvenance.model_validate_json(market["jpx-metadata.json"])
    history = json.loads(market["history-metadata.json"])
    expected_versions = {"jpx": jpx.source_approval_version, "yfinance": history["source_approval_version"]}
    approval_day = frozen.astimezone(ZoneInfo("Asia/Tokyo")).date()
    parsed_approvals = {
        source: SourceApprovalV1.model_validate(yaml.safe_load(body)) for source, body in approvals.items()
    }
    for source, approval in parsed_approvals.items():
        if (
            approval.source_id != source
            or approval.approval_version != expected_versions[source]
            or approval.status != "approved"
            or approval.effective_on is None
            or approval.recheck_due_on is None
            or not (
                date.fromisoformat(approval.effective_on) <= approval_day < date.fromisoformat(approval.recheck_due_on)
            )
        ):
            raise ValueError("freeze_source_approval_mismatch")
    retrieved = {"jpx": jpx.retrieved_at, "yfinance": history["retrieved_at"]}
    for stamp in retrieved.values():
        if datetime.fromisoformat(stamp.replace("Z", "+00:00")) > frozen:
            raise ValueError("freeze_precedes_acquisition")

    records: list[dict[str, object]] = []
    source_specs = (
        ("jpx", "jpx.xlsx", jpx.reference, index.jpx_verification.snapshot_on, "Asia/Tokyo"),
        (
            "yfinance",
            "prices.csv",
            f"yfinance:Ticker.history:{history['symbol']}",
            history["arguments"]["start"],
            history["exchange_timezone"] or "Asia/Tokyo",
        ),
    )
    for source, path, reference, start, timezone in source_specs:
        approval = parsed_approvals[source]
        end = (
            index.jpx_verification.snapshot_on
            if source == "jpx"
            else (date.fromisoformat(history["arguments"]["end"]) - timedelta(days=1)).isoformat()
        )
        records.extend(
            (
                {
                    "evidence_id": f"{source}-metadata",
                    "layer": "source_metadata",
                    "source_id": source,
                    "source_approval_reference": _reference(
                        "source_approval", approval.artifact_id, "detailed-analysis.source-approval", approvals[source]
                    ).model_dump(),
                    "provider": approval.provider,
                    "service": approval.service,
                    "dataset": approval.dataset,
                    "published_at": None,
                    "first_available_at": None,
                    "updated_at": None,
                    "retrieved_at": retrieved[source],
                    "applicable_period": {"start_date": start, "end_date": end},
                    "timezone": timezone,
                    "reference": reference,
                    "content_sha256": _digest(market[path]),
                    "data_version": f"source-approval-v{approval.approval_version}",
                },
                {
                    "evidence_id": f"{source}-raw",
                    "layer": "raw",
                    "source_metadata_evidence_id": f"{source}-metadata",
                    "payload_reference": _reference(
                        "raw_evidence_payload",
                        f"{source}-payload",
                        "detailed-analysis.raw-evidence-payload",
                        market[path],
                    ).model_dump(),
                    "content_sha256": _digest(market[path]),
                    "data_version": "retained-bytes-v1",
                },
            )
        )
    if "calendar.json" not in market:
        normalized = index.normalized_evidence.model_dump(mode="json")
        input_ids = {
            "jpx.xlsx": "jpx-raw",
            "jpx-metadata.json": "jpx-metadata",
            "prices.csv": "yfinance-raw",
            "history-metadata.json": "yfinance-metadata",
        }
        normalized["input_evidence_ids"] = [input_ids[name] for name in index.normalized_evidence.input_evidence_ids]
        records.append(normalized)
    evidence = EvidenceSetV1.model_validate(
        {
            "schema_id": "detailed-analysis.evidence-set",
            "schema_version": 1,
            "task_id": task.task_id,
            "artifact_id": "evidence-set-v1",
            "evidence_set_version": 1,
            "state": "frozen",
            "previous_evidence_set": None,
            "evidence_frozen_at": frozen_at,
            "freshness_checked_at": frozen_at,
            "analysis_completed_at": None,
            "records": records,
            "freshness_assessments": [
                {
                    "category": "listing-currentness",
                    "status": "missing",
                    "evidence_ids": ["jpx-raw"],
                    "requirement": "Current listing eligibility must be checked separately from the retained snapshot.",
                    "detail": "The retained JPX workbook proves only membership at its snapshot date.",
                },
                {
                    "category": "price-acceptance",
                    "status": "missing",
                    "evidence_ids": ["yfinance-raw"],
                    "requirement": "Price quality and the latest completed trading session require validation.",
                    "detail": "Market preparation quality passed; final-session freshness is not checked."
                    if index.price_quality_passed
                    else "Market preparation has unresolved price quality issues.",
                },
            ],
            "invalidations": [],
        }
    )
    evidence_bytes = _serialize(evidence)
    task_reference = _reference(
        "detailed_analysis_task", "task", "detailed-analysis.detailed-analysis-task", market["task.json"]
    )
    evidence_reference = _reference("evidence_set", evidence.artifact_id, evidence.schema_id, evidence_bytes)
    artifact_sources: list[tuple[ArtifactReferenceV1, str, str]] = [
        (task_reference, "task-intake", task.task_accepted_at),
        (evidence_reference, "evidence-freeze", frozen_at),
    ]
    for record in evidence.records:
        if isinstance(record, SourceMetadataEvidenceV1):
            artifact_sources.append((record.source_approval_reference, "source-policy", frozen_at))
        elif isinstance(record, RawEvidenceV1):
            artifact_sources.append(
                (record.payload_reference, "source-acquisition", retrieved[record.evidence_id.removesuffix("-raw")])
            )
    manifest = ExecutionManifestV1.model_validate(
        {
            "schema_id": "detailed-analysis.execution-manifest",
            "schema_version": 1,
            "task_id": task.task_id,
            "artifact_id": "execution-manifest-v1",
            "manifest_version": 1,
            "updated_at": frozen_at,
            "task_reference": task_reference.model_dump(),
            "current_state": "running",
            "state_transitions": [
                {
                    "transition_id": "transition-1",
                    "operation_id": "operation-freeze-1",
                    "from_state": "initial",
                    "to_state": "running",
                    "occurred_at": frozen_at,
                    "reason_code": "evidence-frozen",
                    "reason": "Validated retained market evidence was frozen with unresolved freshness recorded.",
                }
            ],
            "current_checkpoint": None,
            "current_evidence_set_reference": evidence_reference.model_dump(),
            "current_evidence_set_version": 1,
            "agent_execution_references": [],
            "instruction_application_references": [],
            "external_request_event_references": [],
            "operation_request_references": [],
            "operation_result_references": [],
            "artifacts": [
                {
                    "reference": reference.model_dump(),
                    "producer_role": producer_role,
                    "created_at": created_at,
                    "validation_status": "valid",
                    "reused_from_task_id": None,
                    "reuse_revalidated": False,
                }
                for reference, producer_role, created_at in artifact_sources
            ],
            "final_analysis_result_reference": None,
            "classification_pending_present": True,
            "unresolved_high_or_critical_finding_present": False,
            "unresolved_material_dispute_present": False,
            "primary_review_passed": False,
            "human_decision_required": False,
            "automatic_retry_allowed": False,
        }
    )
    return {
        **{f"market/{name}": body for name, body in market.items()},
        **{f"approvals/{source}.yaml": body for source, body in approvals.items()},
        "artifact-paths.json": (
            json.dumps(
                {
                    "task": "market/task.json",
                    "jpx-payload": "market/jpx.xlsx",
                    "yfinance-payload": "market/prices.csv",
                    parsed_approvals["jpx"].artifact_id: "approvals/jpx.yaml",
                    parsed_approvals["yfinance"].artifact_id: "approvals/yfinance.yaml",
                    evidence.artifact_id: "evidence-set.json",
                    **(
                        {index.normalized_evidence.evidence_id: "market/normalized.json"}
                        if "calendar.json" not in market
                        else {}
                    ),
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode(),
        "evidence-set.json": evidence_bytes,
        "execution-manifest.json": _serialize(manifest),
    }


def validate_frozen_market(files: Mapping[str, bytes]) -> None:
    """Rebuild the entire publication from retained inputs and compare exact bytes."""
    market = {name.removeprefix("market/"): body for name, body in files.items() if name.startswith("market/")}
    approvals = {
        name.removeprefix("approvals/").removesuffix(".yaml"): body
        for name, body in files.items()
        if name.startswith("approvals/") and name.endswith(".yaml")
    }
    evidence = EvidenceSetV1.model_validate_json(files["evidence-set.json"])
    ExecutionManifestV1.model_validate_json(files["execution-manifest.json"])
    if dict(files) != build_frozen_market(market, approvals, evidence.evidence_frozen_at):
        raise ValueError("frozen_market_binding_invalid")


def main(argv: list[str] | None = None) -> int:
    """Publish or verify an initial market evidence set."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--market", type=Path, required=True)
    prepare.add_argument("--approvals", type=Path, required=True)
    prepare.add_argument("--runs", type=Path, required=True)
    prepare.add_argument("--destination", required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("--bundle", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            market = read_bundle(args.market)
            versions = {
                "jpx": JpxSnapshotProvenance.model_validate_json(market["jpx-metadata.json"]).source_approval_version,
                "yfinance": json.loads(market["history-metadata.json"])["source_approval_version"],
            }
            approvals = {
                source: (args.approvals / source / f"v{version}.yaml").read_bytes()
                for source, version in versions.items()
            }
            files = build_frozen_market(market, approvals, datetime.now(UTC).isoformat())
            publish_preparation(args.runs, args.destination, files, validator=validate_frozen_market)
            bundle = args.runs / args.destination
        else:
            bundle = args.bundle
        validate_frozen_market(read_bundle(bundle))
        print(f"frozen_market: valid; bundle={bundle}; analysis_ready=false")
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(f"frozen_market_failed: {type(error).__name__}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
