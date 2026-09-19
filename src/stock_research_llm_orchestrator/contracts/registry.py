"""Registry for exact contract version dispatch."""

from collections.abc import Iterator

from stock_research_llm_orchestrator.contracts.base import StrictContractModel
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.agent_execution import AgentExecutionV1
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.analysis import SynthesisResultV1, WorkerAnalysisV1
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


type ContractKey = tuple[str, int]
type ContractModel = type[StrictContractModel]


class DuplicateContractError(ValueError):
    """Raised when a contract key is registered more than once."""


class UnsupportedContractError(LookupError):
    """Raised when an exact contract key is unavailable."""


class ContractRegistry:
    """Map stable schema identifiers and versions to Pydantic models."""

    def __init__(self) -> None:
        """Create an empty registry."""
        self._models: dict[ContractKey, ContractModel] = {}

    def register(self, schema_id: str, schema_version: int, model: ContractModel) -> None:
        """Register one exact contract key.

        Args:
            schema_id: Stable schema identifier without a version suffix.
            schema_version: Positive integer contract version.
            model: Strict Pydantic contract model.

        Raises:
            DuplicateContractError: If the key is already registered.
            ValueError: If the key is structurally invalid.
        """
        if not schema_id:
            raise ValueError("schema_id must not be empty")
        if schema_version < 1:
            raise ValueError("schema_version must be positive")
        key = (schema_id, schema_version)
        if key in self._models:
            raise DuplicateContractError(f"contract is already registered: {schema_id} v{schema_version}")
        self._models[key] = model

    def get(self, schema_id: str, schema_version: int) -> ContractModel:
        """Return the model for an exact key without fallback."""
        try:
            return self._models[(schema_id, schema_version)]
        except KeyError as exc:
            raise UnsupportedContractError(f"unsupported contract: {schema_id} v{schema_version}") from exc

    def items(self) -> Iterator[tuple[ContractKey, ContractModel]]:
        """Iterate over contracts in deterministic key order."""
        for key in sorted(self._models):
            yield key, self._models[key]


default_registry = ContractRegistry()
default_registry.register("detailed-analysis.agent-execution", 1, AgentExecutionV1)
default_registry.register("detailed-analysis.worker-analysis", 1, WorkerAnalysisV1)
default_registry.register("detailed-analysis.synthesis-result", 1, SynthesisResultV1)
default_registry.register("detailed-analysis.primary-review", 1, PrimaryReviewV1)
default_registry.register("detailed-analysis.review-response", 1, ReviewResponseV1)
default_registry.register("detailed-analysis.dispute", 1, DisputeV1)
default_registry.register("detailed-analysis.audit-request", 1, AuditRequestV1)
default_registry.register("detailed-analysis.audit-result", 1, AuditResultV1)
default_registry.register("detailed-analysis.human-decision", 1, HumanDecisionV1)
default_registry.register("detailed-analysis.execution-manifest", 1, ExecutionManifestV1)
default_registry.register("detailed-analysis.operation-request", 1, OperationRequestV1)
default_registry.register("detailed-analysis.operation-result", 1, OperationResultV1)
default_registry.register("detailed-analysis.detailed-analysis-task", 1, DetailedAnalysisTaskV1)
default_registry.register("detailed-analysis.evidence-set", 1, EvidenceSetV1)
default_registry.register("detailed-analysis.external-request-event", 1, ExternalRequestEventV1)
default_registry.register("detailed-analysis.final-analysis-result", 1, FinalAnalysisResultV1)
default_registry.register("detailed-analysis.instruction-application", 1, InstructionApplicationV1)
default_registry.register("detailed-analysis.detailed-analysis-policy", 1, DetailedAnalysisPolicyV1)
default_registry.register("detailed-analysis.review-audit-policy", 1, ReviewAuditPolicyV1)
default_registry.register("detailed-analysis.session-continuation-policy", 1, SessionContinuationPolicyV1)
default_registry.register("detailed-analysis.web-research-policy", 1, WebResearchPolicyV1)
default_registry.register("detailed-analysis.market-profile", 1, MarketProfileV1)
default_registry.register("detailed-analysis.source-approval", 1, SourceApprovalV1)
default_registry.register("detailed-analysis.source-profile", 1, SourceProfileV1)
default_registry.register("detailed-analysis.research-context", 1, ResearchContextV1)
default_registry.register("detailed-analysis.search-record", 1, SearchRecordV1)
default_registry.register("detailed-analysis.common-evidence-update", 1, CommonEvidenceUpdateV1)
default_registry.register("detailed-analysis.validation-error", 1, ValidationErrorV1)
