"""Freeze attributed excerpt snapshots without asserting final evidence readiness."""

import json
from collections.abc import Mapping
from datetime import datetime
from hashlib import sha256

import yaml

from stock_research_llm_orchestrator.contracts.base import ArtifactReferenceV1, StrictContractModel
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.evidence import EvidenceSetV1
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.policies import (
    DetailedAnalysisPolicyV1,
    SourceApprovalV1,
)
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.task import DetailedAnalysisTaskV1
from stock_research_llm_orchestrator.preparation.research_draft import DraftInput, validate_bundle
from stock_research_llm_orchestrator.sources.yfinance.normalization import NormalizedPrices


def serialize(value: StrictContractModel) -> bytes:
    """Serialize an immutable artifact to the exact retained bytes."""
    return value.model_dump_json(indent=2).encode() + b"\n"


def reference(kind: str, artifact_id: str, schema_id: str, body: bytes) -> ArtifactReferenceV1:
    """Bind a reference to the actual retained bytes."""
    return ArtifactReferenceV1(
        artifact_type=kind,
        artifact_id=artifact_id,
        schema_id=schema_id,
        schema_version=1,
        sha256=sha256(body).hexdigest(),
    )


def excerpt_text(source_id: str, text: str) -> str:
    """Keep a minimal observed price snapshot instead of transferring a bulk series."""
    if source_id != "yfinance":
        return text
    payload = json.loads(text)
    prices = NormalizedPrices.model_validate_json(json.dumps(payload["normalized_prices"]))
    if not prices.rows:
        raise ValueError("research_price_snapshot_empty")
    return json.dumps(
        {
            "currency": prices.currency,
            "timezone": prices.timezone,
            "last_observed_row": prices.rows[-1].model_dump(mode="json"),
            "quality_passed": payload["quality_passed"],
            "issues": payload["issues"],
            "limitations": payload["limitations"],
            "note": "One retained daily observation, not a live quote or a verified complete price history. "
            "The date label does not establish a precise price observation time.",
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def build_input(
    task_bytes: bytes,
    prior: Mapping[str, bytes],
    policy_bytes: bytes,
    approvals: Mapping[str, bytes],
    frozen_at: str,
) -> dict[str, bytes]:
    """Retain an exact excerpt snapshot and its upstream input without importing conclusions."""
    validate_bundle(prior)
    task = DetailedAnalysisTaskV1.model_validate_json(task_bytes)
    data = DraftInput.model_validate_json(prior["input.json"])
    policy = DetailedAnalysisPolicyV1.model_validate(yaml.safe_load(policy_bytes))
    frozen = datetime.fromisoformat(frozen_at.replace("Z", "+00:00"))
    if (
        frozen.utcoffset() is None
        or frozen < datetime.fromisoformat(task.task_accepted_at.replace("Z", "+00:00"))
        or frozen < datetime.fromisoformat(policy.effective_at.replace("Z", "+00:00"))
        or task.security.security_code != data.security_code
        or task.security.mic != data.exchange
        or task.evaluation_policy_version != policy.policy_version
    ):
        raise ValueError("research_task_input_mismatch")
    expected_approvals = {f"{item.source_id}-v{item.approval_version}" for item in data.evidence}
    if set(approvals) != expected_approvals:
        raise ValueError("research_approval_inventory_invalid")
    parent_bytes = task_bytes
    payload = task.model_dump(mode="json")
    payload.update(
        task_id="research-" + sha256(task_bytes + prior["input.json"] + frozen_at.encode()).hexdigest()[:24],
        parent_task_id=task.task_id,
        task_accepted_at=frozen_at,
    )
    task = DetailedAnalysisTaskV1.model_validate_json(json.dumps(payload))
    files = {
        "parent-task.json": parent_bytes,
        "task.json": serialize(task),
        "policy.yaml": policy_bytes,
        **{f"prior/{name}": body for name, body in prior.items()},
        **{f"approvals/{name}": body for name, body in approvals.items()},
    }
    records: list[dict[str, object]] = []
    excerpts = []
    for index, item in enumerate(data.evidence):
        key = f"{item.source_id}-v{item.approval_version}"
        approval_bytes = approvals[key]
        if item.source_id == "issuer-ir":
            if item.approval_version != 1 or not approval_bytes:
                raise ValueError("research_issuer_policy_invalid")
            approval_ref = reference(
                "owner_source_policy", "issuer-ir-owner-policy", "detailed-analysis.owner-source-policy", approval_bytes
            )
        else:
            approval = SourceApprovalV1.model_validate(yaml.safe_load(approval_bytes))
            if (
                approval.source_id != item.source_id
                or approval.approval_version != item.approval_version
                or approval.status != "approved"
                or not approval.external_agent_transfer_allowed
            ):
                raise ValueError("research_source_approval_invalid")
            approval_ref = reference("source_approval", approval.artifact_id, approval.schema_id, approval_bytes)
        retrieved = datetime.fromisoformat(item.retrieved_at.replace("Z", "+00:00"))
        if retrieved > frozen:
            raise ValueError("research_snapshot_precedes_acquisition")
        text = excerpt_text(item.source_id, item.text)
        body = text.encode()
        files[f"excerpts/{index}.txt"] = body
        digest = sha256(body).hexdigest()
        metadata_id = f"snapshot-metadata-{index}"
        day = retrieved.date().isoformat()
        records.extend(
            (
                {
                    "evidence_id": metadata_id,
                    "layer": "source_metadata",
                    "source_id": item.source_id,
                    "source_approval_reference": approval_ref.model_dump(),
                    "provider": item.source_id,
                    "service": "Retained attributed excerpt",
                    "dataset": "Excerpt acquisition snapshot",
                    "published_at": None,
                    "first_available_at": None,
                    "updated_at": None,
                    "retrieved_at": item.retrieved_at,
                    "applicable_period": {"start_date": day, "end_date": day},
                    "timezone": str(retrieved.tzinfo),
                    "reference": item.source_url or item.source_reference,
                    "content_sha256": digest,
                    "data_version": "excerpt-snapshot-v1",
                },
                {
                    "evidence_id": item.evidence_id,
                    "layer": "raw",
                    "source_metadata_evidence_id": metadata_id,
                    "payload_reference": reference(
                        "excerpt_payload", f"excerpt-{index}", "detailed-analysis.raw-evidence-payload", body
                    ).model_dump(),
                    "content_sha256": digest,
                    "data_version": "excerpt-snapshot-v1",
                },
            )
        )
        excerpts.append(
            {
                "evidence_id": item.evidence_id,
                "source_id": item.source_id,
                "source_url": item.source_url,
                "source_reference": item.source_reference,
                "retrieved_at": item.retrieved_at,
                "upstream_source_sha256": item.source_sha256,
                "excerpt_sha256": digest,
                "text": text,
            }
        )
    evidence = EvidenceSetV1.model_validate(
        {
            "schema_id": "detailed-analysis.evidence-set",
            "schema_version": 1,
            "task_id": task.task_id,
            "artifact_id": "research-excerpt-evidence-v1",
            "evidence_set_version": 1,
            "state": "frozen",
            "previous_evidence_set": None,
            "evidence_frozen_at": frozen_at,
            "freshness_checked_at": frozen_at,
            "analysis_completed_at": None,
            "records": records,
            "freshness_assessments": [
                {
                    "category": "final-freshness",
                    "status": "missing",
                    "evidence_ids": [item.evidence_id for item in data.evidence],
                    "requirement": (
                        "Final freshness and independent search coverage must be verified before completion."
                    ),
                    "detail": (
                        "Only local snapshot consistency was checked. "
                        "Acquisition dates are not financial periods, "
                        "publication dates, or a verification of current market eligibility."
                    ),
                }
            ],
            "invalidations": [],
        }
    )
    files["evidence-set.json"] = serialize(evidence)
    files["common-input.json"] = (json.dumps(excerpts, ensure_ascii=False, indent=2) + "\n").encode()
    return files


def validate_input(files: Mapping[str, bytes]) -> None:
    """Rebuild the selected excerpts and all provenance from retained upstream bytes."""
    prior = {key.removeprefix("prior/"): body for key, body in files.items() if key.startswith("prior/")}
    approvals = {key.removeprefix("approvals/"): body for key, body in files.items() if key.startswith("approvals/")}
    evidence = EvidenceSetV1.model_validate_json(files["evidence-set.json"])
    expected = build_input(
        files["parent-task.json"], prior, files["policy.yaml"], approvals, evidence.evidence_frozen_at
    )
    if dict(files) != expected:
        raise ValueError("research_input_binding_invalid")
