"""Tests for exact-version contract registry behavior."""

import pytest

from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.agent_execution import AgentExecutionV1
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.analysis import (
    SynthesisResultV1,
    WorkerAnalysisV1,
)
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.dispute_audit import (
    AuditRequestV1,
    AuditResultV1,
    DisputeV1,
    HumanDecisionV1,
)
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.evidence import EvidenceSetV1
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.external_requests import ExternalRequestEventV1
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.final_output import FinalAnalysisResultV1
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.instructions import InstructionApplicationV1
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.operations import (
    OperationRequestV1,
    OperationResultV1,
)
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.policies import (
    DetailedAnalysisPolicyV1,
    MarketProfileV1,
    ReviewAuditPolicyV1,
    SessionContinuationPolicyV1,
    SourceApprovalV1,
    SourceProfileV1,
    WebResearchPolicyV1,
)
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.research import (
    CommonEvidenceUpdateV1,
    ResearchContextV1,
    SearchRecordV1,
)
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.review import PrimaryReviewV1, ReviewResponseV1
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.runtime import ExecutionManifestV1
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.task import DetailedAnalysisTaskV1
from stock_research_llm_orchestrator.contracts.errors import ValidationErrorV1
from stock_research_llm_orchestrator.contracts.registry import (
    ContractRegistry,
    DuplicateContractError,
    UnsupportedContractError,
    default_registry,
)


def test_registry_rejects_duplicate_key() -> None:
    """Reject duplicate schema identifier and version registrations."""
    registry = ContractRegistry()
    registry.register("detailed-analysis.validation-error", 1, ValidationErrorV1)

    with pytest.raises(DuplicateContractError):
        registry.register("detailed-analysis.validation-error", 1, ValidationErrorV1)


def test_registry_does_not_fallback_to_another_version() -> None:
    """Require an exact schema version match."""
    registry = ContractRegistry()
    registry.register("detailed-analysis.validation-error", 1, ValidationErrorV1)

    with pytest.raises(UnsupportedContractError):
        registry.get("detailed-analysis.validation-error", 2)


def test_default_registry_contains_detailed_analysis_task() -> None:
    """Dispatch the approved public task contract by its exact key."""
    assert default_registry.get("detailed-analysis.detailed-analysis-task", 1) is DetailedAnalysisTaskV1


@pytest.mark.parametrize(
    ("schema_id", "model"),
    [
        ("detailed-analysis.agent-execution", AgentExecutionV1),
        ("detailed-analysis.execution-manifest", ExecutionManifestV1),
        ("detailed-analysis.operation-request", OperationRequestV1),
        ("detailed-analysis.operation-result", OperationResultV1),
        ("detailed-analysis.final-analysis-result", FinalAnalysisResultV1),
        ("detailed-analysis.instruction-application", InstructionApplicationV1),
    ],
)
def test_default_registry_contains_runtime_contracts(schema_id: str, model: type[object]) -> None:
    """Dispatch task runtime and logical operation contracts by exact keys."""
    assert default_registry.get(schema_id, 1) is model


def test_default_registry_contains_evidence_set() -> None:
    """Dispatch the public evidence-set contract by its exact key."""
    assert default_registry.get("detailed-analysis.evidence-set", 1) is EvidenceSetV1


@pytest.mark.parametrize(
    ("schema_id", "model"),
    [
        ("detailed-analysis.worker-analysis", WorkerAnalysisV1),
        ("detailed-analysis.synthesis-result", SynthesisResultV1),
    ],
)
def test_default_registry_contains_analysis_contracts(schema_id: str, model: type[object]) -> None:
    """Dispatch public analysis contracts by exact keys."""
    assert default_registry.get(schema_id, 1) is model


@pytest.mark.parametrize(
    ("schema_id", "model"),
    [
        ("detailed-analysis.primary-review", PrimaryReviewV1),
        ("detailed-analysis.review-response", ReviewResponseV1),
        ("detailed-analysis.dispute", DisputeV1),
        ("detailed-analysis.audit-request", AuditRequestV1),
        ("detailed-analysis.audit-result", AuditResultV1),
        ("detailed-analysis.human-decision", HumanDecisionV1),
    ],
)
def test_default_registry_contains_review_and_audit_contracts(schema_id: str, model: type[object]) -> None:
    """Dispatch each public review and audit contract by its exact key."""
    assert default_registry.get(schema_id, 1) is model


def test_default_registry_contains_external_request_event() -> None:
    """Dispatch the public request audit contract by its exact key."""
    assert default_registry.get("detailed-analysis.external-request-event", 1) is ExternalRequestEventV1


@pytest.mark.parametrize(
    ("schema_id", "model"),
    [
        ("detailed-analysis.detailed-analysis-policy", DetailedAnalysisPolicyV1),
        ("detailed-analysis.review-audit-policy", ReviewAuditPolicyV1),
        ("detailed-analysis.session-continuation-policy", SessionContinuationPolicyV1),
        ("detailed-analysis.web-research-policy", WebResearchPolicyV1),
        ("detailed-analysis.market-profile", MarketProfileV1),
        ("detailed-analysis.source-approval", SourceApprovalV1),
        ("detailed-analysis.source-profile", SourceProfileV1),
    ],
)
def test_default_registry_contains_policy_contracts(schema_id: str, model: type[object]) -> None:
    """Dispatch each public policy and profile by its exact key."""
    assert default_registry.get(schema_id, 1) is model


@pytest.mark.parametrize(
    ("schema_id", "model"),
    [
        ("detailed-analysis.research-context", ResearchContextV1),
        ("detailed-analysis.search-record", SearchRecordV1),
        ("detailed-analysis.common-evidence-update", CommonEvidenceUpdateV1),
    ],
)
def test_default_registry_contains_research_contracts(schema_id: str, model: type[object]) -> None:
    """Dispatch each public research contract by its exact key."""
    assert default_registry.get(schema_id, 1) is model
