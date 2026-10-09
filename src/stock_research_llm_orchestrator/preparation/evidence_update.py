"""Adopt a validated JPX follow-up as a new immutable evidence-set version."""

import argparse
import json
from collections.abc import Mapping
from datetime import UTC, date, datetime
from hashlib import sha256
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from stock_research_llm_orchestrator.contracts.base import ArtifactReferenceV1
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.evidence import (
    EvidenceSetV1,
    SourceMetadataEvidenceV1,
)
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.policies import SourceApprovalV1
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.runtime import ExecutionManifestV1
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.task import DetailedAnalysisTaskV1
from stock_research_llm_orchestrator.preparation.evidence_freeze import validate_frozen_market
from stock_research_llm_orchestrator.preparation.jpx_research_follow_up import validate_jpx_follow_up
from stock_research_llm_orchestrator.preparation.market_evidence import JpxSnapshotProvenance
from stock_research_llm_orchestrator.preparation.research_draft import DraftInput
from stock_research_llm_orchestrator.preparation.storage import publish_preparation, read_bundle


def _reference(artifact_type: str, artifact_id: str, schema_id: str, body: bytes) -> ArtifactReferenceV1:
    return ArtifactReferenceV1(
        artifact_type=artifact_type,
        artifact_id=artifact_id,
        schema_id=schema_id,
        schema_version=1,
        sha256=sha256(body).hexdigest(),
    )


def _timestamp(value: str) -> datetime:
    observed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if observed.utcoffset() is None:
        raise ValueError("evidence_update_time_requires_offset")
    return observed


def build_jpx_evidence_update(
    previous: Mapping[str, bytes], follow_up: Mapping[str, bytes], frozen_at: str
) -> dict[str, bytes]:
    """Revalidate both publications and retain them beneath a new evidence-set version."""
    validate_frozen_market(previous)
    validate_jpx_follow_up(follow_up)
    frozen = _timestamp(frozen_at)
    old_set = EvidenceSetV1.model_validate_json(previous["evidence-set.json"])
    old_manifest = ExecutionManifestV1.model_validate_json(previous["execution-manifest.json"])
    if old_set.evidence_set_version != 1 or old_manifest.manifest_version != 1:
        raise ValueError("evidence_update_requires_initial_version")
    if frozen < _timestamp(old_set.evidence_frozen_at):
        raise ValueError("evidence_update_precedes_previous_freeze")
    task = DetailedAnalysisTaskV1.model_validate_json(previous["market/task.json"])
    prior_input = DraftInput.model_validate_json(follow_up["prior-input.json"])
    if task.task_id != old_set.task_id or task.task_id != old_manifest.task_id:
        raise ValueError("evidence_update_task_mismatch")
    if prior_input.security_code != task.security.security_code or prior_input.exchange != task.security.mic:
        raise ValueError("evidence_update_security_mismatch")
    for source, path in (("jpx", "market/jpx.xlsx"), ("yfinance", "market/prices.csv")):
        matches = [
            item
            for item in prior_input.evidence
            if item.source_id == source and item.source_sha256 == sha256(previous[path]).hexdigest()
        ]
        if len(matches) != 1:
            raise ValueError("evidence_update_prior_input_mismatch")

    provenance = JpxSnapshotProvenance.model_validate_json(follow_up["jpx-provenance.json"])
    if _timestamp(provenance.retrieved_at) > frozen:
        raise ValueError("evidence_update_precedes_acquisition")
    approval_body = previous["approvals/jpx.yaml"]
    approval = SourceApprovalV1.model_validate(yaml.safe_load(approval_body))
    approval_day = frozen.astimezone(ZoneInfo("Asia/Tokyo")).date()
    if (
        approval.source_id != "jpx"
        or approval.approval_version != provenance.source_approval_version
        or approval.status != "approved"
        or approval.effective_on is None
        or approval.recheck_due_on is None
        or not (date.fromisoformat(approval.effective_on) <= approval_day < date.fromisoformat(approval.recheck_due_on))
    ):
        raise ValueError("evidence_update_source_approval_invalid")
    excerpt = json.loads(follow_up["source.bin"])
    snapshot_on = excerpt["verification"]["snapshot_on"]
    old_jpx = next((record for record in old_set.records if record.evidence_id == "jpx-metadata"), None)
    if not isinstance(old_jpx, SourceMetadataEvidenceV1):
        raise ValueError("evidence_update_prior_jpx_missing")
    if date.fromisoformat(snapshot_on) <= date.fromisoformat(old_jpx.applicable_period.end_date):
        raise ValueError("evidence_update_snapshot_not_newer")
    workbook = follow_up["jpx-workbook.xlsx"]
    content_hash = sha256(workbook).hexdigest()
    suffix = content_hash[:16]
    metadata_id = f"jpx-{suffix}-metadata"
    raw_id = f"jpx-{suffix}-raw"
    raw_reference = _reference(
        "raw_evidence_payload", f"jpx-{suffix}-payload", "detailed-analysis.raw-evidence-payload", workbook
    )
    approval_reference = _reference(
        "source_approval", approval.artifact_id, "detailed-analysis.source-approval", approval_body
    )
    records = [record.model_dump(mode="json") for record in old_set.records]
    records.extend(
        (
            {
                "evidence_id": metadata_id,
                "layer": "source_metadata",
                "source_id": "jpx",
                "source_approval_reference": approval_reference.model_dump(),
                "provider": approval.provider,
                "service": approval.service,
                "dataset": approval.dataset,
                "published_at": None,
                "first_available_at": None,
                "updated_at": None,
                "retrieved_at": provenance.retrieved_at,
                "applicable_period": {"start_date": snapshot_on, "end_date": snapshot_on},
                "timezone": "Asia/Tokyo",
                "reference": provenance.reference,
                "content_sha256": content_hash,
                "data_version": f"snapshot-{snapshot_on}",
            },
            {
                "evidence_id": raw_id,
                "layer": "raw",
                "source_metadata_evidence_id": metadata_id,
                "payload_reference": raw_reference.model_dump(),
                "content_sha256": content_hash,
                "data_version": "retained-bytes-v1",
            },
        )
    )
    assessments = [item.model_dump(mode="json") for item in old_set.freshness_assessments]
    for assessment in assessments:
        if assessment["category"] == "listing-currentness":
            assessment["status"] = "missing"
            assessment["evidence_ids"] = [raw_id]
            assessment["detail"] = f"The retained JPX workbook proves membership on {snapshot_on}, not current status."
    previous_reference = _reference(
        "evidence_set", old_set.artifact_id, old_set.schema_id, previous["evidence-set.json"]
    )
    evidence = EvidenceSetV1.model_validate(
        {
            **old_set.model_dump(mode="json"),
            "artifact_id": "evidence-set-v2",
            "evidence_set_version": 2,
            "previous_evidence_set": {
                "evidence_set_version": 1,
                "artifact_reference": previous_reference.model_dump(),
            },
            "evidence_frozen_at": frozen_at,
            "freshness_checked_at": frozen_at,
            "records": records,
            "freshness_assessments": assessments,
        }
    )
    evidence_bytes = evidence.model_dump_json(indent=2).encode() + b"\n"
    current_reference = _reference("evidence_set", evidence.artifact_id, evidence.schema_id, evidence_bytes)
    artifacts = [item.model_dump(mode="json") for item in old_manifest.artifacts]
    artifacts.extend(
        (
            {
                "reference": raw_reference.model_dump(),
                "producer_role": "source-acquisition",
                "created_at": provenance.retrieved_at,
                "validation_status": "valid",
                "reused_from_task_id": None,
                "reuse_revalidated": False,
            },
            {
                "reference": current_reference.model_dump(),
                "producer_role": "evidence-freeze",
                "created_at": frozen_at,
                "validation_status": "valid",
                "reused_from_task_id": None,
                "reuse_revalidated": False,
            },
        )
    )
    manifest = ExecutionManifestV1.model_validate(
        {
            **old_manifest.model_dump(mode="json"),
            "artifact_id": "execution-manifest-v2",
            "manifest_version": 2,
            "updated_at": frozen_at,
            "current_evidence_set_reference": current_reference.model_dump(),
            "current_evidence_set_version": 2,
            "artifacts": artifacts,
        }
    )
    old_paths = json.loads(previous["artifact-paths.json"])
    paths = {artifact_id: f"previous/{path}" for artifact_id, path in old_paths.items()}
    paths[raw_reference.artifact_id] = "follow-up/jpx-workbook.xlsx"
    paths[current_reference.artifact_id] = "evidence-set.json"
    return {
        **{f"previous/{name}": body for name, body in previous.items()},
        **{f"follow-up/{name}": body for name, body in follow_up.items()},
        "artifact-paths.json": (json.dumps(paths, indent=2, sort_keys=True) + "\n").encode(),
        "evidence-set.json": evidence_bytes,
        "execution-manifest.json": manifest.model_dump_json(indent=2).encode() + b"\n",
    }


def validate_jpx_evidence_update(files: Mapping[str, bytes]) -> None:
    """Replay the exact version update from retained prior and acquisition bytes."""
    previous = {name.removeprefix("previous/"): body for name, body in files.items() if name.startswith("previous/")}
    follow_up = {name.removeprefix("follow-up/"): body for name, body in files.items() if name.startswith("follow-up/")}
    evidence = EvidenceSetV1.model_validate_json(files["evidence-set.json"])
    ExecutionManifestV1.model_validate_json(files["execution-manifest.json"])
    if dict(files) != build_jpx_evidence_update(previous, follow_up, evidence.evidence_frozen_at):
        raise ValueError("evidence_update_binding_invalid")


def main(argv: list[str] | None = None) -> int:
    """Publish or verify a JPX evidence-set update."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--previous", type=Path, required=True)
    prepare.add_argument("--follow-up", type=Path, required=True)
    prepare.add_argument("--runs", type=Path, required=True)
    prepare.add_argument("--destination", required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("--bundle", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            files = build_jpx_evidence_update(
                read_bundle(args.previous), read_bundle(args.follow_up), datetime.now(UTC).isoformat()
            )
            publish_preparation(args.runs, args.destination, files, validator=validate_jpx_evidence_update)
            bundle = args.runs / args.destination
        else:
            bundle = args.bundle
        validate_jpx_evidence_update(read_bundle(bundle))
        print(f"jpx_evidence_update: valid; bundle={bundle}; listing_currentness=missing")
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(f"jpx_evidence_update_failed: {type(error).__name__}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
