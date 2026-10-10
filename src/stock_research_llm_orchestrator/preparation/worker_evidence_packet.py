"""Select approved per-security JPX facts for a Codex worker input."""

import json
from collections.abc import Mapping
from datetime import datetime
from hashlib import sha256
from typing import Literal
from zoneinfo import ZoneInfo

import yaml

from stock_research_llm_orchestrator.contracts.base import StrictContractModel
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.evidence import (
    EvidenceSetV1,
    FreshnessAssessmentV1,
    RawEvidenceV1,
    SourceMetadataEvidenceV1,
)
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.policies import SourceApprovalV1
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.research import ResearchContextV1
from stock_research_llm_orchestrator.preparation.market_evidence import JpxSnapshotProvenance
from stock_research_llm_orchestrator.preparation.research_context import build_codex_research_context
from stock_research_llm_orchestrator.sources.jpx.verification import JpxSecurityVerification


class CodexEvidencePacket(StrictContractModel):
    """Minimal source-bound facts without source workbooks or price series."""

    scope: Literal["per_security_jpx_facts_only"] = "per_security_jpx_facts_only"
    task_id: str
    research_context_sha256: str
    evidence_set_sha256: str
    evidence_set_version: Literal[2] = 2
    evidence_id: str
    source_metadata_evidence_id: str
    source_id: Literal["jpx"] = "jpx"
    transfer_approval_version: Literal[2] = 2
    transfer_approval_sha256: str
    source_reference: str
    source_content_sha256: str
    retrieved_at: str
    snapshot_on: str
    security_code: str
    name: str
    market_segment: str
    industry_33_name: str | None
    industry_17_name: str | None
    freshness_assessments: tuple[FreshnessAssessmentV1, ...]


def build_codex_evidence_packet(
    evidence_bundle: Mapping[str, bytes],
    context: ResearchContextV1,
    policy_bytes: bytes,
    transfer_approval_bytes: bytes,
    transfer_at: datetime,
) -> CodexEvidencePacket:
    """Recheck the exact evidence and live transfer approval before selecting facts."""
    if (
        transfer_at.utcoffset() is None
        or datetime.fromisoformat(context.created_at.replace("Z", "+00:00")) > transfer_at
    ):
        raise ValueError("codex_packet_transfer_time_invalid")
    expected_context = build_codex_research_context(evidence_bundle, policy_bytes, context.created_at)
    if context != expected_context or context.evidence_set_version != 2:
        raise ValueError("codex_packet_context_mismatch")
    approval = SourceApprovalV1.model_validate(yaml.safe_load(transfer_approval_bytes))
    day = transfer_at.astimezone(ZoneInfo("Asia/Tokyo")).date().isoformat()
    if (
        approval.source_id != "jpx"
        or approval.approval_version != 2
        or approval.status != "approved"
        or approval.usage_entity != "private_individual_internal_analysis"
        or not approval.external_agent_transfer_allowed
        or approval.effective_on is None
        or approval.effective_on > day
        or approval.recheck_due_on is None
        or day >= approval.recheck_due_on
    ):
        raise ValueError("codex_packet_transfer_not_approved")
    evidence = EvidenceSetV1.model_validate_json(evidence_bundle["evidence-set.json"])
    excerpt = json.loads(evidence_bundle["follow-up/source.bin"])
    provenance = JpxSnapshotProvenance.model_validate_json(evidence_bundle["follow-up/jpx-provenance.json"])
    verification = JpxSecurityVerification.model_validate(excerpt["verification"])
    issue = verification.issue
    if issue is None or issue.market_segment is None:
        raise ValueError("codex_packet_issue_missing")
    content_hash = sha256(evidence_bundle["follow-up/jpx-workbook.xlsx"]).hexdigest()
    suffix = content_hash[:16]
    metadata_id = f"jpx-{suffix}-metadata"
    raw_id = f"jpx-{suffix}-raw"
    records = {record.evidence_id: record for record in evidence.records}
    metadata = records.get(metadata_id)
    raw = records.get(raw_id)
    if (
        not isinstance(metadata, SourceMetadataEvidenceV1)
        or not isinstance(raw, RawEvidenceV1)
        or raw.source_metadata_evidence_id != metadata_id
        or raw.content_sha256 != content_hash
        or metadata.content_sha256 != content_hash
        or metadata.source_id != "jpx"
        or metadata.retrieved_at != excerpt["retrieved_at"]
        or metadata.applicable_period.end_date != verification.snapshot_on
        or metadata.reference != provenance.reference
        or provenance.sha256 != content_hash
        or issue.code != context.security.identifier.security_code
        or issue.mic != context.security.identifier.mic
        or raw_id not in context.common_evidence_ids
    ):
        raise ValueError("codex_packet_source_mismatch")
    return CodexEvidencePacket(
        task_id=context.task_id,
        research_context_sha256=sha256(context.model_dump_json(indent=2).encode() + b"\n").hexdigest(),
        evidence_set_sha256=context.evidence_set_reference.sha256,
        evidence_id=raw_id,
        source_metadata_evidence_id=metadata_id,
        transfer_approval_sha256=sha256(transfer_approval_bytes).hexdigest(),
        source_reference=metadata.reference,
        source_content_sha256=content_hash,
        retrieved_at=metadata.retrieved_at,
        snapshot_on=verification.snapshot_on,
        security_code=issue.code,
        name=issue.name,
        market_segment=issue.market_segment,
        industry_33_name=issue.industry_33_name,
        industry_17_name=issue.industry_17_name,
        freshness_assessments=evidence.freshness_assessments,
    )
