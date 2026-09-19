"""Worker analysis and synthesis contracts for detailed analysis version 1."""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from stock_research_llm_orchestrator.contracts.base import (
    ArtifactReferenceV1,
    Identifier,
    NonEmptyString,
    StrictContractModel,
    Timestamp,
)
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.evidence import _parse_rfc3339
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.task import SecurityIdentifierV1


class ClaimType(StrEnum):
    """Required semantic classification for an analysis claim."""

    FACT = "fact"
    INFERENCE = "inference"
    HYPOTHESIS = "hypothesis"


class Confidence(StrEnum):
    """Qualitative confidence supported by the cited evidence."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Evaluability(StrEnum):
    """Whether the approved policy has enough usable evidence for evaluation."""

    EVALUABLE = "evaluable"
    NOT_EVALUABLE = "not_evaluable"


class AssessmentStatus(StrEnum):
    """Four-level horizon assessment kept separate from task execution state."""

    PASS = "pass"
    CONDITIONAL = "conditional"
    INVESTIGATE = "investigate"
    REJECT = "reject"


class AnalysisPerspective(StrEnum):
    """Five perspectives required for both evaluation horizons."""

    FINANCIAL = "financial"
    VALUATION = "valuation"
    BUSINESS = "business"
    TECHNICAL_REFERENCE = "technical_reference"
    RISK_AND_COUNTEREVIDENCE = "risk_and_counterevidence"


ClaimTypeValue = Annotated[ClaimType, Field(strict=False)]
ConfidenceValue = Annotated[Confidence, Field(strict=False)]
EvaluabilityValue = Annotated[Evaluability, Field(strict=False)]
AssessmentStatusValue = Annotated[AssessmentStatus, Field(strict=False)]
AnalysisPerspectiveValue = Annotated[AnalysisPerspective, Field(strict=False)]


class AnalysisClaimV1(StrictContractModel):
    """One typed claim with evidence and explicit uncertainty."""

    claim_id: Identifier
    perspective: AnalysisPerspectiveValue
    statement: NonEmptyString
    claim_type: ClaimTypeValue
    evidence_ids: tuple[Identifier, ...] = Field(strict=False)
    counterevidence_ids: tuple[Identifier, ...] = Field(strict=False)
    uncertainty: NonEmptyString
    material: bool

    @model_validator(mode="after")
    def validate_claim_evidence(self) -> AnalysisClaimV1:
        """Require verified evidence for statements classified as facts."""
        if self.claim_type is ClaimType.FACT and not self.evidence_ids:
            raise ValueError("fact claims require at least one evidence ID")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("claim evidence IDs must be unique")
        if len(self.counterevidence_ids) != len(set(self.counterevidence_ids)):
            raise ValueError("claim counterevidence IDs must be unique")
        return self


class HorizonAssessmentV1(StrictContractModel):
    """Four-level assessment for one evaluable horizon."""

    status: AssessmentStatusValue
    confidence: ConfidenceValue
    rationale: NonEmptyString
    condition_or_research_needs: tuple[NonEmptyString, ...] = Field(strict=False)


class HorizonAnalysisV1(StrictContractModel):
    """Worker analysis kept independent for one investment horizon."""

    horizon: Literal["medium_term", "long_term"]
    summary: NonEmptyString
    positive_factors: tuple[NonEmptyString, ...] = Field(strict=False)
    negative_factors: tuple[NonEmptyString, ...] = Field(strict=False)
    thesis: tuple[NonEmptyString, ...] = Field(strict=False, min_length=1)
    counter_thesis: tuple[NonEmptyString, ...] = Field(strict=False, min_length=1)
    valuation_view: NonEmptyString
    watch_conditions: tuple[NonEmptyString, ...] = Field(strict=False)
    missing_information: tuple[NonEmptyString, ...] = Field(strict=False)
    claims: tuple[AnalysisClaimV1, ...] = Field(strict=False, min_length=1)
    evaluability: EvaluabilityValue
    assessment: HorizonAssessmentV1 | None

    @model_validator(mode="after")
    def validate_assessment_presence(self) -> HorizonAnalysisV1:
        """Leave the four-level result unset when a horizon is not evaluable."""
        if self.evaluability is Evaluability.EVALUABLE and self.assessment is None:
            raise ValueError("evaluable horizons require an assessment")
        if self.evaluability is Evaluability.NOT_EVALUABLE and self.assessment is not None:
            raise ValueError("not_evaluable horizons must not contain an assessment")
        perspectives = {claim.perspective for claim in self.claims}
        missing = set(AnalysisPerspective) - perspectives
        if missing:
            raise ValueError("each horizon must contain claims for all five perspectives")
        return self


class CrossHorizonSummaryV1(StrictContractModel):
    """Comparison across horizons without a new combined assessment."""

    agreements: tuple[NonEmptyString, ...] = Field(strict=False)
    differences: tuple[NonEmptyString, ...] = Field(strict=False)
    summary: NonEmptyString


class EntryExitReferenceContextV1(StrictContractModel):
    """Current-price context that remains distinct from trading instructions."""

    price_as_of: Timestamp
    current_price_context: NonEmptyString
    entry_reference_conditions: tuple[NonEmptyString, ...] = Field(strict=False)
    technical_exit_reference_conditions: tuple[NonEmptyString, ...] = Field(strict=False)
    thesis_invalidation_conditions: tuple[NonEmptyString, ...] = Field(strict=False)
    upcoming_event_risks: tuple[NonEmptyString, ...] = Field(strict=False)
    volatility_context: NonEmptyString
    evidence_ids: tuple[Identifier, ...] = Field(strict=False, min_length=1)
    missing_information: tuple[NonEmptyString, ...] = Field(strict=False)
    reference_only_not_trade_instruction: Literal[True]

    @field_validator("price_as_of")
    @classmethod
    def ensure_valid_price_as_of(cls, value: str) -> str:
        """Validate the price observation timestamp."""
        _parse_rfc3339(value, "price_as_of")
        return value


class WorkerAnalysisV1(StrictContractModel):
    """Structured output from one independent normal worker."""

    schema_id: Literal["detailed-analysis.worker-analysis"]
    schema_version: Literal[1]
    task_id: Identifier
    artifact_id: Identifier
    agent_run_id: Identifier
    worker_role: Literal["codex_worker", "claude_worker"]
    created_at: Timestamp
    security: SecurityIdentifierV1
    evidence_set_reference: ArtifactReferenceV1
    evidence_set_version: int = Field(ge=1)
    evidence_frozen_at: Timestamp
    freshness_checked_at: Timestamp
    evaluation_policy_reference: ArtifactReferenceV1
    horizon_analyses: tuple[HorizonAnalysisV1, ...] = Field(strict=False, min_length=2, max_length=2)
    cross_horizon_summary: CrossHorizonSummaryV1
    entry_exit_context: EntryExitReferenceContextV1

    @field_validator("created_at", "evidence_frozen_at", "freshness_checked_at")
    @classmethod
    def ensure_valid_timestamps(cls, value: str, info: object) -> str:
        """Validate worker and evidence lifecycle timestamps."""
        _parse_rfc3339(value, getattr(info, "field_name", "timestamp"))
        return value

    @model_validator(mode="after")
    def validate_worker_analysis(self) -> WorkerAnalysisV1:
        """Require exact inputs, horizon order, and task-unique claim IDs."""
        if self.evidence_set_reference.schema_id != "detailed-analysis.evidence-set":
            raise ValueError("worker analysis must reference an evidence-set artifact")
        if self.evaluation_policy_reference.schema_id != "detailed-analysis.detailed-analysis-policy":
            raise ValueError("worker analysis must reference the detailed-analysis policy")
        if tuple(item.horizon for item in self.horizon_analyses) != ("medium_term", "long_term"):
            raise ValueError("worker analysis requires medium_term followed by long_term")
        claim_ids = [claim.claim_id for horizon in self.horizon_analyses for claim in horizon.claims]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("claim IDs must be unique within a task worker analysis")
        frozen_at = _parse_rfc3339(self.evidence_frozen_at, "evidence_frozen_at")
        freshness_at = _parse_rfc3339(self.freshness_checked_at, "freshness_checked_at")
        created_at = _parse_rfc3339(self.created_at, "created_at")
        if freshness_at < frozen_at or created_at < freshness_at:
            raise ValueError("worker analysis timestamps must follow evidence lifecycle order")
        return self


class WorkerAnalysisInputV1(StrictContractModel):
    """Exact independent worker artifact used by synthesis."""

    worker_role: Literal["codex_worker", "claude_worker"]
    artifact_reference: ArtifactReferenceV1


class SynthesisDifferenceV1(StrictContractModel):
    """Unaveraged difference between worker claims or assessments."""

    difference_id: Identifier
    horizon: Literal["medium_term", "long_term", "cross_horizon"]
    codex_claim_ids: tuple[Identifier, ...] = Field(strict=False)
    claude_claim_ids: tuple[Identifier, ...] = Field(strict=False)
    codex_position: NonEmptyString
    claude_position: NonEmptyString
    evidence_ids: tuple[Identifier, ...] = Field(strict=False)
    missing_information: tuple[NonEmptyString, ...] = Field(strict=False)
    resolution_status: Literal["unresolved", "resolved", "research_required"]
    resolution_rationale: NonEmptyString


class SynthesizedHorizonResultV1(StrictContractModel):
    """Synthesis result for one horizon, preserving sources and differences."""

    horizon: Literal["medium_term", "long_term"]
    summary: NonEmptyString
    agreed_claim_ids: tuple[Identifier, ...] = Field(strict=False)
    difference_ids: tuple[Identifier, ...] = Field(strict=False)
    missing_information: tuple[NonEmptyString, ...] = Field(strict=False)
    counterevidence_conditions: tuple[NonEmptyString, ...] = Field(strict=False)
    evaluability: EvaluabilityValue
    assessment: HorizonAssessmentV1 | None

    @model_validator(mode="after")
    def validate_assessment_presence(self) -> SynthesizedHorizonResultV1:
        """Apply the same evaluability boundary used by worker analysis."""
        if self.evaluability is Evaluability.EVALUABLE and self.assessment is None:
            raise ValueError("evaluable synthesized horizons require an assessment")
        if self.evaluability is Evaluability.NOT_EVALUABLE and self.assessment is not None:
            raise ValueError("not_evaluable synthesized horizons must not contain an assessment")
        return self


class SynthesisResultV1(StrictContractModel):
    """Orchestrator synthesis that preserves both independent worker positions."""

    schema_id: Literal["detailed-analysis.synthesis-result"]
    schema_version: Literal[1]
    task_id: Identifier
    artifact_id: Identifier
    created_at: Timestamp
    worker_inputs: tuple[WorkerAnalysisInputV1, ...] = Field(strict=False, min_length=2, max_length=2)
    evidence_set_reference: ArtifactReferenceV1
    evidence_set_version: int = Field(ge=1)
    agreed_claim_ids: tuple[Identifier, ...] = Field(strict=False)
    differences: tuple[SynthesisDifferenceV1, ...] = Field(strict=False)
    horizon_results: tuple[SynthesizedHorizonResultV1, ...] = Field(strict=False, min_length=2, max_length=2)
    cross_horizon_summary: CrossHorizonSummaryV1
    entry_exit_context: EntryExitReferenceContextV1
    differences_preserved_without_voting_or_averaging: Literal[True]

    @field_validator("created_at")
    @classmethod
    def ensure_valid_created_at(cls, value: str) -> str:
        """Validate synthesis creation time."""
        _parse_rfc3339(value, "created_at")
        return value

    @model_validator(mode="after")
    def validate_synthesis(self) -> SynthesisResultV1:
        """Require exactly two worker families and consistent difference references."""
        if tuple(item.worker_role for item in self.worker_inputs) != ("codex_worker", "claude_worker"):
            raise ValueError("synthesis requires Codex then Claude worker inputs")
        for item in self.worker_inputs:
            if item.artifact_reference.schema_id != "detailed-analysis.worker-analysis":
                raise ValueError("synthesis worker inputs must reference worker-analysis artifacts")
        if self.evidence_set_reference.schema_id != "detailed-analysis.evidence-set":
            raise ValueError("synthesis must reference an evidence-set artifact")
        if tuple(item.horizon for item in self.horizon_results) != ("medium_term", "long_term"):
            raise ValueError("synthesis requires medium_term followed by long_term")
        difference_ids = [item.difference_id for item in self.differences]
        if len(difference_ids) != len(set(difference_ids)):
            raise ValueError("synthesis difference IDs must be unique")
        referenced = {identifier for result in self.horizon_results for identifier in result.difference_ids}
        if referenced - set(difference_ids):
            raise ValueError("horizon results must reference declared synthesis differences")
        return self
