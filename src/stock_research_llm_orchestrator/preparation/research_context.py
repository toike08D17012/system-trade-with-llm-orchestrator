"""Bind one Codex research context to validated task and evidence references."""

import json
from collections.abc import Mapping
from datetime import datetime
from hashlib import sha256

import yaml

from stock_research_llm_orchestrator.contracts.base import ArtifactReferenceV1
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.evidence import (
    EvidenceSetV1,
    SourceMetadataEvidenceV1,
)
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.policies import DetailedAnalysisPolicyV1
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.research import ResearchContextV1
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.runtime import ExecutionManifestV1
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.task import (
    DetailedAnalysisTaskV1,
    SecurityInformationV1,
)
from stock_research_llm_orchestrator.preparation.evidence_freeze import validate_frozen_market
from stock_research_llm_orchestrator.preparation.evidence_update import validate_jpx_evidence_update
from stock_research_llm_orchestrator.preparation.market_evidence import MarketPreparationIndex
from stock_research_llm_orchestrator.sources.jpx.current_list import JpxListedIssue
from stock_research_llm_orchestrator.sources.yfinance.normalization import NormalizedPrices


def _reference(artifact_type: str, artifact_id: str, schema_id: str, body: bytes) -> ArtifactReferenceV1:
    return ArtifactReferenceV1(
        artifact_type=artifact_type,
        artifact_id=artifact_id,
        schema_id=schema_id,
        schema_version=1,
        sha256=sha256(body).hexdigest(),
    )


def build_codex_research_context(
    evidence_bundle: Mapping[str, bytes], policy_bytes: bytes, created_at: str
) -> ResearchContextV1:
    """Create a reference-only input from a fully replayed evidence bundle."""
    evidence = EvidenceSetV1.model_validate_json(evidence_bundle["evidence-set.json"])
    if evidence.evidence_set_version == 1:
        validate_frozen_market(evidence_bundle)
        prefix = ""
        index = MarketPreparationIndex.model_validate_json(evidence_bundle["market/index.json"])
        issue = index.jpx_verification.issue
    elif evidence.evidence_set_version == 2:
        validate_jpx_evidence_update(evidence_bundle)
        prefix = "previous/"
        excerpt = json.loads(evidence_bundle["follow-up/source.bin"])
        issue = JpxListedIssue.model_validate(excerpt["verification"]["issue"])
    else:
        raise ValueError("research_context_evidence_version_unsupported")
    if issue is None or issue.market_segment is None:
        raise ValueError("research_context_jpx_issue_missing")
    manifest = ExecutionManifestV1.model_validate_json(evidence_bundle["execution-manifest.json"])
    task_body = evidence_bundle[f"{prefix}market/task.json"]
    task = DetailedAnalysisTaskV1.model_validate_json(task_body)
    prices = NormalizedPrices.model_validate_json(evidence_bundle[f"{prefix}market/normalized.json"])
    policy = DetailedAnalysisPolicyV1.model_validate(yaml.safe_load(policy_bytes))
    stamp = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    if stamp.utcoffset() is None or stamp < datetime.fromisoformat(evidence.evidence_frozen_at.replace("Z", "+00:00")):
        raise ValueError("research_context_time_invalid")
    if (
        task.task_id != evidence.task_id
        or task.task_id != manifest.task_id
        or manifest.task_reference.sha256 != sha256(task_body).hexdigest()
        or manifest.current_evidence_set_reference.sha256 != sha256(evidence_bundle["evidence-set.json"]).hexdigest()
        or manifest.current_evidence_set_version != evidence.evidence_set_version
        or task.evaluation_policy_version != policy.policy_version
        or task.market.mic != policy.market_mic
        or datetime.fromisoformat(policy.effective_at.replace("Z", "+00:00")) > stamp
        or issue.code != task.security.security_code
        or issue.mic != task.security.mic
        or prices.currency is None
    ):
        raise ValueError("research_context_input_mismatch")
    policy_reference = _reference("detailed_analysis_policy", policy.artifact_id, policy.schema_id, policy_bytes)
    return ResearchContextV1(
        schema_id="detailed-analysis.research-context",
        schema_version=1,
        task_id=task.task_id,
        artifact_id=f"research-context-codex-v{evidence.evidence_set_version}",
        created_at=created_at,
        recipient_role="codex_worker",
        task_reference=manifest.task_reference,
        evidence_set_reference=manifest.current_evidence_set_reference,
        evidence_set_version=evidence.evidence_set_version,
        security=SecurityInformationV1(
            identifier=task.security,
            name=issue.name,
            currency=prices.currency,
            sector=issue.industry_33_name or issue.industry_17_name or "Unknown in retained JPX snapshot",
            market_segment=issue.market_segment,
        ),
        common_evidence_ids=tuple(record.evidence_id for record in evidence.records),
        source_metadata_evidence_ids=tuple(
            record.evidence_id for record in evidence.records if isinstance(record, SourceMetadataEvidenceV1)
        ),
        research_questions=(
            "What verified evidence supports or limits each required analysis perspective and horizon?",
            "Which missing or stale facts prevent evaluation and require approved follow-up acquisition?",
        ),
        policy_references=(policy_reference,),
    )
