"""Dispute, conditional audit, and human-decision contracts for version 1."""

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
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.analysis import ConfidenceValue
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.evidence import _parse_rfc3339
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.review import (
    FindingClassification,
    FindingClassificationValue,
    MaterialityAssessmentV1,
)


class AuditGateDecision(StrEnum):
    """Deterministic result of the six approved audit conditions."""

    START_AUDIT = "start_audit"
    DO_NOT_AUDIT = "do_not_audit"
    HUMAN_DECISION_REQUIRED = "human_decision_required"


class AuditOpinion(StrEnum):
    """Valid neutral audit opinions from ADR-0004."""

    SUPPORT_INTERPRETATION_1 = "support_interpretation_1"
    SUPPORT_INTERPRETATION_2 = "support_interpretation_2"
    SUPPORT_NEITHER = "support_neither"
    INDETERMINATE_MORE_EVIDENCE_POSSIBLE = "indeterminate_more_evidence_possible"
    INDETERMINATE_NO_MORE_EVIDENCE = "indeterminate_no_more_evidence"
    HUMAN_JUDGMENT_REQUIRED = "human_judgment_required"


class AuditExecutionResult(StrEnum):
    """Physical execution result kept separate from the audit opinion."""

    SUCCEEDED = "succeeded"
    INVALID_OUTPUT = "invalid_output"
    EXECUTION_FAILED = "execution_failed"
    CANCELLED = "cancelled"


class AuditRecommendedNextState(StrEnum):
    """Workflow destination derived from a valid opinion or execution failure."""

    PRIMARY_REVIEW = "primary_review"
    RESEARCH = "research"
    HUMAN_DECISION = "human_decision"
    FAILED = "failed"


AuditGateDecisionValue = Annotated[AuditGateDecision, Field(strict=False)]
AuditOpinionValue = Annotated[AuditOpinion, Field(strict=False)]
AuditExecutionResultValue = Annotated[AuditExecutionResult, Field(strict=False)]
AuditRecommendedNextStateValue = Annotated[AuditRecommendedNextState, Field(strict=False)]


class DisputePositionV1(StrictContractModel):
    """One substantiated interpretation without producer identity."""

    statement: NonEmptyString
    rationale: tuple[NonEmptyString, ...] = Field(strict=False, min_length=1)
    evidence_ids: tuple[Identifier, ...] = Field(strict=False)
    evidence_absence_reason: NonEmptyString | None
    acknowledged_uncertainties: tuple[NonEmptyString, ...] = Field(strict=False)
    falsification_conditions: tuple[NonEmptyString, ...] = Field(strict=False, min_length=1)

    @model_validator(mode="after")
    def validate_support(self) -> DisputePositionV1:
        """Require evidence or an explicit explanation that evidence is absent."""
        if not self.evidence_ids and self.evidence_absence_reason is None:
            raise ValueError("a dispute position requires evidence or an evidence absence reason")
        if self.evidence_ids and self.evidence_absence_reason is not None:
            raise ValueError("a dispute position with evidence must not claim that evidence is absent")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("dispute position evidence IDs must be unique")
        return self


class AgreedFactV1(StrictContractModel):
    """One verified fact agreed by both interpretations."""

    statement: NonEmptyString
    evidence_ids: tuple[Identifier, ...] = Field(strict=False, min_length=1)


class DisputeV1(StrictContractModel):
    """One material unresolved interpretation difference after normal handling."""

    schema_id: Literal["detailed-analysis.dispute"]
    schema_version: Literal[1]
    task_id: Identifier
    artifact_id: Identifier
    dispute_id: Identifier
    created_at: Timestamp
    source_finding_ids: tuple[Identifier, ...] = Field(strict=False, min_length=1)
    target_claim_ids: tuple[Identifier, ...] = Field(strict=False, min_length=1)
    horizon: Literal["medium_term", "long_term", "cross_horizon"]
    primary_classification: FindingClassificationValue
    policy_references: tuple[ArtifactReferenceV1, ...] = Field(strict=False, min_length=1)
    unresolved_question: NonEmptyString
    interpretation_1: DisputePositionV1
    interpretation_2: DisputePositionV1
    agreed_facts: tuple[AgreedFactV1, ...] = Field(strict=False)
    primary_review_completed: Literal[True]
    response_completed: Literal[True]
    permitted_limited_research_completed: Literal[True]
    mechanically_resolvable: Literal[False]
    materiality: MaterialityAssessmentV1
    parent_dispute_ids: tuple[Identifier, ...] = Field(strict=False)

    @field_validator("created_at")
    @classmethod
    def ensure_valid_created_at(cls, value: str) -> str:
        """Validate the dispute creation timestamp."""
        _parse_rfc3339(value, "created_at")
        return value

    @model_validator(mode="after")
    def validate_dispute_scope(self) -> DisputeV1:
        """Allow only unresolved material interpretation differences."""
        eligible_classifications = {
            FindingClassification.POLICY_APPLICATION_DIFFERENCE,
            FindingClassification.LOGICAL_GAP,
            FindingClassification.FUTURE_HYPOTHESIS_DIFFERENCE,
            FindingClassification.RISK_MATERIALITY_DIFFERENCE,
        }
        if self.primary_classification not in eligible_classifications:
            raise ValueError("factual, evidence, schema, and style findings must not become audit disputes")
        if not self.materiality.is_material:
            raise ValueError("a dispute requires a material unresolved interpretation difference")
        for values, label in (
            (self.source_finding_ids, "source finding IDs"),
            (self.target_claim_ids, "target claim IDs"),
            (self.parent_dispute_ids, "parent dispute IDs"),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{label} must be unique")
        if self.dispute_id in self.parent_dispute_ids:
            raise ValueError("a dispute must not list itself as a parent")
        return self


class AuditGateConditionsV1(StrictContractModel):
    """The six approved conditions plus the non-terminal pending guard."""

    primary_review_response_and_research_complete: bool
    two_substantiated_positions_unresolved: bool
    mechanically_resolvable: bool
    material: bool
    neutral_packet_valid: bool
    already_validly_audited: bool
    classification_pending_present: bool
    decision: AuditGateDecisionValue

    @model_validator(mode="after")
    def validate_decision(self) -> AuditGateConditionsV1:
        """Require the gate result to follow the approved decision flow."""
        core_ready = (
            self.primary_review_response_and_research_complete
            and self.two_substantiated_positions_unresolved
            and not self.mechanically_resolvable
            and self.material
            and not self.classification_pending_present
        )
        if core_ready and self.neutral_packet_valid and not self.already_validly_audited:
            calculated = AuditGateDecision.START_AUDIT
        elif core_ready and (not self.neutral_packet_valid or self.already_validly_audited):
            calculated = AuditGateDecision.HUMAN_DECISION_REQUIRED
        else:
            calculated = AuditGateDecision.DO_NOT_AUDIT
        if self.decision is not calculated:
            raise ValueError("audit decision must equal the approved six-condition gate calculation")
        return self


class AuditEvidenceItemV1(StrictContractModel):
    """Minimal verified evidence references placed in the neutral packet."""

    evidence_id: Identifier
    content_reference: ArtifactReferenceV1
    source_metadata_reference: ArtifactReferenceV1


class NeutralityAttestationV1(StrictContractModel):
    """Explicit packet validation required before an audit may start."""

    producer_names_included: Literal[False]
    preferred_side_included: Literal[False]
    reviewer_preference_included: Literal[False]
    orchestrator_provisional_conclusion_included: Literal[False]
    recommendation_included: Literal[False]
    out_of_scope_content_included: Literal[False]
    important_evidence_omitted: Literal[False]
    validated: Literal[True]


class RequestedAuditChecksV1(StrictContractModel):
    """Fixed neutral audit questions for the MVP."""

    determine_evidence_support: Literal[True]
    check_policy_application: Literal[True]
    identify_missing_assumptions: Literal[True]
    recommend_next_state: Literal[True]


class NeutralAuditPacketV1(StrictContractModel):
    """One anonymized, dispute-scoped packet for a read-only auditor."""

    task_id: Identifier
    dispute_id: Identifier
    question: NonEmptyString
    horizon: Literal["medium_term", "long_term", "cross_horizon"]
    target_claim_ids: tuple[Identifier, ...] = Field(strict=False, min_length=1)
    policy_references: tuple[ArtifactReferenceV1, ...] = Field(strict=False, min_length=1)
    agreed_facts: tuple[AgreedFactV1, ...] = Field(strict=False)
    unresolved_issues: tuple[NonEmptyString, ...] = Field(strict=False, min_length=1)
    interpretation_1: DisputePositionV1
    interpretation_2: DisputePositionV1
    evidence_bundle: tuple[AuditEvidenceItemV1, ...] = Field(strict=False, min_length=1)
    requested_checks: RequestedAuditChecksV1
    neutrality: NeutralityAttestationV1


class AuditRequestV1(StrictContractModel):
    """Authorized logical audit request for exactly one dispute."""

    schema_id: Literal["detailed-analysis.audit-request"]
    schema_version: Literal[1]
    task_id: Identifier
    artifact_id: Identifier
    audit_id: Identifier
    dispute_id: Identifier
    dispute_reference: ArtifactReferenceV1
    created_at: Timestamp
    logical_audit_sequence: Literal[1]
    audit_workspace_read_only: Literal[True]
    automatic_retry_allowed: Literal[False]
    gate: AuditGateConditionsV1
    packet: NeutralAuditPacketV1

    @field_validator("created_at")
    @classmethod
    def ensure_valid_created_at(cls, value: str) -> str:
        """Validate the request timestamp."""
        _parse_rfc3339(value, "created_at")
        return value

    @model_validator(mode="after")
    def validate_audit_request(self) -> AuditRequestV1:
        """Only a successful gate may produce a logical audit request."""
        if self.dispute_reference.schema_id != "detailed-analysis.dispute":
            raise ValueError("an audit request must reference a dispute artifact")
        if self.gate.decision is not AuditGateDecision.START_AUDIT:
            raise ValueError("an audit request requires a start_audit gate decision")
        if self.packet.task_id != self.task_id:
            raise ValueError("the neutral packet task must match the audit request task")
        if self.packet.dispute_id != self.dispute_id:
            raise ValueError("the neutral packet must describe the referenced dispute")
        return self


class AuditEvidenceAssessmentV1(StrictContractModel):
    """Auditor assessment of one evidence item used by the dispute."""

    evidence_id: Identifier
    relevance: Literal["low", "medium", "high"]
    interpretation: NonEmptyString


class AuditPositionAssessmentV1(StrictContractModel):
    """Supported and unsupported parts of one anonymous interpretation."""

    supported_parts: tuple[NonEmptyString, ...] = Field(strict=False)
    unsupported_parts: tuple[NonEmptyString, ...] = Field(strict=False)


class AuditResultV1(StrictContractModel):
    """Validated audit opinion or a separate physical execution failure."""

    schema_id: Literal["detailed-analysis.audit-result"]
    schema_version: Literal[1]
    task_id: Identifier
    artifact_id: Identifier
    audit_id: Identifier
    dispute_id: Identifier
    created_at: Timestamp
    execution_result: AuditExecutionResultValue
    opinion: AuditOpinionValue | None
    confidence: ConfidenceValue | None
    summary: NonEmptyString
    evidence_assessments: tuple[AuditEvidenceAssessmentV1, ...] = Field(strict=False)
    interpretation_1_assessment: AuditPositionAssessmentV1 | None
    interpretation_2_assessment: AuditPositionAssessmentV1 | None
    established_facts: tuple[NonEmptyString, ...] = Field(strict=False)
    reasonable_inferences: tuple[NonEmptyString, ...] = Field(strict=False)
    unresolved_hypotheses: tuple[NonEmptyString, ...] = Field(strict=False)
    missing_assumptions: tuple[NonEmptyString, ...] = Field(strict=False)
    omitted_counterarguments: tuple[NonEmptyString, ...] = Field(strict=False)
    additional_evidence_needed: tuple[NonEmptyString, ...] = Field(strict=False)
    schema_valid: bool
    references_valid: bool
    dispute_scope_valid: bool
    logical_audit_counted: bool
    automatic_retry_allowed: Literal[False]
    recommended_next_state: AuditRecommendedNextStateValue
    rationale: NonEmptyString

    @field_validator("created_at")
    @classmethod
    def ensure_valid_created_at(cls, value: str) -> str:
        """Validate the result timestamp."""
        _parse_rfc3339(value, "created_at")
        return value

    @model_validator(mode="after")
    def validate_opinion_and_application(self) -> AuditResultV1:
        """Separate execution failure and map valid opinions deterministically."""
        valid_output = (
            self.execution_result is AuditExecutionResult.SUCCEEDED
            and self.schema_valid
            and self.references_valid
            and self.dispute_scope_valid
        )
        if valid_output:
            if self.opinion is None or self.confidence is None:
                raise ValueError("a valid audit result requires an opinion and confidence")
            if self.interpretation_1_assessment is None or self.interpretation_2_assessment is None:
                raise ValueError("a valid audit result requires assessments of both interpretations")
            mapping = {
                AuditOpinion.SUPPORT_INTERPRETATION_1: AuditRecommendedNextState.PRIMARY_REVIEW,
                AuditOpinion.SUPPORT_INTERPRETATION_2: AuditRecommendedNextState.PRIMARY_REVIEW,
                AuditOpinion.SUPPORT_NEITHER: AuditRecommendedNextState.PRIMARY_REVIEW,
                AuditOpinion.INDETERMINATE_MORE_EVIDENCE_POSSIBLE: AuditRecommendedNextState.RESEARCH,
                AuditOpinion.INDETERMINATE_NO_MORE_EVIDENCE: AuditRecommendedNextState.HUMAN_DECISION,
                AuditOpinion.HUMAN_JUDGMENT_REQUIRED: AuditRecommendedNextState.HUMAN_DECISION,
            }
            expected_next_state = mapping[self.opinion]
        else:
            if self.opinion is not None or self.confidence is not None:
                raise ValueError("an invalid or failed execution must not contain an adopted audit opinion")
            if self.interpretation_1_assessment is not None or self.interpretation_2_assessment is not None:
                raise ValueError("an invalid or failed execution must not contain adopted position assessments")
            expected_next_state = AuditRecommendedNextState.FAILED
        if self.logical_audit_counted is not valid_output:
            raise ValueError("only a schema-, reference-, and scope-valid audit result counts logically")
        if self.recommended_next_state is not expected_next_state:
            raise ValueError("recommended_next_state must follow the approved audit result decision table")
        return self


class HumanDecisionV1(StrictContractModel):
    """Immutable human workflow decision, never an investment recommendation."""

    schema_id: Literal["detailed-analysis.human-decision"]
    schema_version: Literal[1]
    task_id: Identifier
    artifact_id: Identifier
    human_decision_id: Identifier
    target_dispute_id: Identifier | None
    target_finding_id: Identifier | None
    allowed_options: tuple[NonEmptyString, ...] = Field(strict=False, min_length=2)
    selected_option: NonEmptyString
    decider: NonEmptyString
    reason: NonEmptyString
    decided_at: Timestamp
    replaces_human_decision_id: Identifier | None
    workflow_decision_only: Literal[True]

    @field_validator("decided_at")
    @classmethod
    def ensure_valid_decided_at(cls, value: str) -> str:
        """Validate the decision timestamp."""
        _parse_rfc3339(value, "decided_at")
        return value

    @model_validator(mode="after")
    def validate_decision(self) -> HumanDecisionV1:
        """Require one target, a selected option, and immutable replacement linkage."""
        if (self.target_dispute_id is None) is (self.target_finding_id is None):
            raise ValueError("a human decision must target exactly one dispute or finding")
        if len(self.allowed_options) != len(set(self.allowed_options)):
            raise ValueError("human decision options must be unique")
        if self.selected_option not in self.allowed_options:
            raise ValueError("selected_option must be one of the recorded allowed_options")
        if self.replaces_human_decision_id == self.human_decision_id:
            raise ValueError("a human decision must not replace itself")
        return self
