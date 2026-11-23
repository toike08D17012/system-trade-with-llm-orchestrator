"""Provider-neutral provisional artifacts for configuration-driven analysis."""

from typing import Literal

from pydantic import Field, model_validator

from stock_research_llm_orchestrator.contracts.base import (
    ArtifactReferenceV1,
    Identifier,
    NonEmptyString,
    StrictContractModel,
    Timestamp,
)
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.agent_configuration import AgentDefinition
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.analysis import (
    CrossHorizonSummaryV1,
    HorizonAnalysisV1,
    SynthesizedHorizonResultV1,
)
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.objectives import ConclusionBody, SpecialistBody
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.review import (
    ReviewCompletionGateV1,
    ReviewFindingV1,
    ReviewOverallStatusValue,
)


class PriceReferenceContext(StrictContractModel):
    """No observed price timestamp is invented when only a document snapshot exists."""

    price_as_of: Timestamp | None
    current_price_context: NonEmptyString
    entry_reference_conditions: tuple[NonEmptyString, ...] = Field(strict=False)
    technical_exit_reference_conditions: tuple[NonEmptyString, ...] = Field(strict=False)
    thesis_invalidation_conditions: tuple[NonEmptyString, ...] = Field(strict=False)
    upcoming_event_risks: tuple[NonEmptyString, ...] = Field(strict=False)
    volatility_context: NonEmptyString
    evidence_ids: tuple[Identifier, ...] = Field(strict=False)
    missing_information: tuple[NonEmptyString, ...] = Field(strict=False)
    reference_only_not_trade_instruction: Literal[True]


class AggregateBody(StrictContractModel):
    """One track's five-perspective summary, independent of its assigned provider."""

    horizon_analyses: tuple[HorizonAnalysisV1, ...] = Field(strict=False, min_length=2, max_length=2)
    cross_horizon_summary: CrossHorizonSummaryV1
    entry_exit_context: PriceReferenceContext

    @model_validator(mode="after")
    def validate_horizons(self) -> AggregateBody:
        """Require ordered medium and long horizons."""
        if tuple(item.horizon for item in self.horizon_analyses) != ("medium_term", "long_term"):
            raise ValueError("configured_horizon_order_invalid")
        return self


class TrackDifference(StrictContractModel):
    """Preserve both track positions without pretending they are different model families."""

    difference_id: Identifier
    horizon: Literal["medium_term", "long_term"]
    track_a_claim_ids: tuple[Identifier, ...] = Field(strict=False)
    track_b_claim_ids: tuple[Identifier, ...] = Field(strict=False)
    track_a_position: NonEmptyString
    track_b_position: NonEmptyString
    evidence_ids: tuple[Identifier, ...] = Field(strict=False)
    missing_information: tuple[NonEmptyString, ...] = Field(strict=False)
    resolution_rationale: NonEmptyString


class ConfiguredMainBody(ConclusionBody):
    """Neutral synthesis plus objective conclusions from the main agent."""

    agreed_claim_ids: tuple[Identifier, ...] = Field(strict=False)
    differences: tuple[TrackDifference, ...] = Field(strict=False)
    horizon_results: tuple[SynthesizedHorizonResultV1, ...] = Field(strict=False, min_length=2, max_length=2)
    cross_horizon_summary: CrossHorizonSummaryV1
    entry_exit_context: PriceReferenceContext
    differences_preserved_without_voting_or_averaging: Literal[True]

    @model_validator(mode="after")
    def validate_synthesis_horizons(self) -> ConfiguredMainBody:
        """Validate both conclusion and synthesis ordering."""
        if tuple(item.horizon for item in self.horizon_results) != ("medium_term", "long_term"):
            raise ValueError("configured_horizon_order_invalid")
        if len({item.difference_id for item in self.differences}) != len(self.differences):
            raise ValueError("configured_difference_identity_invalid")
        return self


class ConfiguredReviewBody(StrictContractModel):
    """First-round review retains the existing finding and completion-gate semantics."""

    overall_status: ReviewOverallStatusValue
    findings: tuple[ReviewFindingV1, ...] = Field(strict=False)
    completion_gate: ReviewCompletionGateV1
    review_passed: bool

    @model_validator(mode="after")
    def validate_review(self) -> ConfiguredReviewBody:
        """Recalculate passage and forbid unilateral first-round finding resolution."""
        if len({item.finding_id for item in self.findings}) != len(self.findings):
            raise ValueError("configured_finding_identity_invalid")
        if any(item.lifecycle == "resolved" for item in self.findings):
            raise ValueError("configured_first_review_cannot_resolve")
        blocker = any(
            item.classification_pending is not None
            or item.severity in {"high", "critical"}
            or item.materiality.is_material
            for item in self.findings
        )
        passed = (
            self.overall_status in {"approve", "approve_with_changes"}
            and all(self.completion_gate.model_dump().values())
            and not blocker
        )
        if self.review_passed != passed:
            raise ValueError("configured_review_pass_invalid")
        return self


class ConfiguredAnalysisV1(StrictContractModel):
    """Exact role, settings, evidence and payload for one neutral analysis stage."""

    schema_id: Literal["detailed-analysis.configured-analysis"]
    schema_version: Literal[1]
    task_id: Identifier
    artifact_id: Identifier
    role: Literal[
        "track_a_capital",
        "track_a_income",
        "track_b_capital",
        "track_b_income",
        "track_a_aggregator",
        "track_b_aggregator",
        "orchestrator",
        "primary_reviewer",
    ]
    created_at: Timestamp
    agent_alias: Identifier
    agent: AgentDefinition
    configuration_reference: ArtifactReferenceV1
    evidence_set_reference: ArtifactReferenceV1
    input_artifact_references: tuple[ArtifactReferenceV1, ...] = Field(strict=False)
    payload: SpecialistBody | AggregateBody | ConfiguredMainBody | ConfiguredReviewBody

    @model_validator(mode="after")
    def validate_role_payload(self) -> ConfiguredAnalysisV1:
        """Reject an artifact whose role does not match its payload or input contract."""
        expected = (
            SpecialistBody
            if self.role.endswith(("capital", "income"))
            else AggregateBody
            if self.role.endswith("aggregator")
            else ConfiguredMainBody
            if self.role == "orchestrator"
            else ConfiguredReviewBody
        )
        if not isinstance(self.payload, expected):
            raise ValueError("configured_role_payload_invalid")
        if self.configuration_reference.schema_id != "detailed-analysis.agent-configuration":
            raise ValueError("configured_configuration_reference_invalid")
        if self.evidence_set_reference.schema_id != "detailed-analysis.evidence-set":
            raise ValueError("configured_evidence_reference_invalid")
        return self
