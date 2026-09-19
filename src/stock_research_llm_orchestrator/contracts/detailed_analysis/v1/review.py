"""Primary review, finding, response, and re-review contracts for version 1."""

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


class FindingClassification(StrEnum):
    """Approved terminal classification values for review findings."""

    FACTUAL_ERROR = "factual_error"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    SCHEMA_OR_FORMAT_ERROR = "schema_or_format_error"
    POLICY_APPLICATION_DIFFERENCE = "policy_application_difference"
    LOGICAL_GAP = "logical_gap"
    FUTURE_HYPOTHESIS_DIFFERENCE = "future_hypothesis_difference"
    RISK_MATERIALITY_DIFFERENCE = "risk_materiality_difference"
    STYLE_OR_EXPRESSION = "style_or_expression"


class FindingSeverity(StrEnum):
    """Impact of leaving a finding uncorrected."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class FindingLifecycle(StrEnum):
    """Approved lifecycle values for immutable finding history."""

    OPEN = "open"
    RESPONSE_SUBMITTED = "response_submitted"
    RE_REVIEW_REQUIRED = "re_review_required"
    RESOLVED = "resolved"
    DISPUTED = "disputed"
    HUMAN_DECISION_REQUIRED = "human_decision_required"


class ReviewOverallStatus(StrEnum):
    """Primary review disposition for the reviewed synthesis."""

    APPROVE = "approve"
    APPROVE_WITH_CHANGES = "approve_with_changes"
    REWORK = "rework"
    OBJECT = "object"


class ResponseDisposition(StrEnum):
    """Structured response to one immutable finding."""

    ACCEPTED = "accepted"
    RESEARCH_REQUIRED = "research_required"
    REJECTED = "rejected"
    DISPUTED = "disputed"


class ReReviewDecision(StrEnum):
    """Primary reviewer's decision after a finding response."""

    ACCEPTED = "accepted"
    PARTIALLY_ACCEPTED = "partially_accepted"
    REJECTED = "rejected"
    EVIDENCE_OR_CHANGE_REQUIRED = "evidence_or_change_required"
    FINDING_WITHDRAWN = "finding_withdrawn"


class MaterialityImpactType(StrEnum):
    """Targets that can change the information shown to a human."""

    HORIZON_ASSESSMENT = "horizon_assessment"
    EVALUABILITY = "evaluability"
    MAJOR_THESIS = "major_thesis"
    MAJOR_RISK = "major_risk"
    ENTRY_EXIT_REFERENCE = "entry_exit_reference"
    OTHER = "other"


FindingClassificationValue = Annotated[FindingClassification, Field(strict=False)]
FindingSeverityValue = Annotated[FindingSeverity, Field(strict=False)]
FindingLifecycleValue = Annotated[FindingLifecycle, Field(strict=False)]
ReviewOverallStatusValue = Annotated[ReviewOverallStatus, Field(strict=False)]
ResponseDispositionValue = Annotated[ResponseDisposition, Field(strict=False)]
ReReviewDecisionValue = Annotated[ReReviewDecision, Field(strict=False)]
MaterialityImpactTypeValue = Annotated[MaterialityImpactType, Field(strict=False)]


class ClassificationCandidateV1(StrictContractModel):
    """One considered classification and why it was not yet selected."""

    classification: FindingClassificationValue
    rejected_reason: NonEmptyString


class ClassificationPendingV1(StrictContractModel):
    """Temporary state that requires an explicit next classifier."""

    reason: NonEmptyString
    candidates: tuple[ClassificationCandidateV1, ...] = Field(strict=False, min_length=1)
    next_classifier: Literal["primary_reviewer", "orchestrator", "human"]

    @model_validator(mode="after")
    def validate_candidates(self) -> ClassificationPendingV1:
        """Do not record the same candidate more than once."""
        classifications = [candidate.classification for candidate in self.candidates]
        if len(classifications) != len(set(classifications)):
            raise ValueError("classification pending candidates must be unique")
        return self


class MaterialityImpactV1(StrictContractModel):
    """One claimed change between the two interpretations of an issue."""

    impact_id: Identifier
    impact_type: MaterialityImpactTypeValue
    target_id: Identifier
    horizon: Literal["medium_term", "long_term", "cross_horizon", "not_applicable"]
    interpretation_1_effect: NonEmptyString
    interpretation_2_effect: NonEmptyString
    reason: NonEmptyString
    valid: bool


class MaterialityAssessmentV1(StrictContractModel):
    """Materiality derived exactly from valid approved impact types."""

    impacts: tuple[MaterialityImpactV1, ...] = Field(strict=False)
    is_material: bool

    @model_validator(mode="after")
    def validate_derived_materiality(self) -> MaterialityAssessmentV1:
        """Require is_material to equal the deterministic impact calculation."""
        material_types = set(MaterialityImpactType) - {MaterialityImpactType.OTHER}
        calculated = any(impact.valid and impact.impact_type in material_types for impact in self.impacts)
        if self.is_material is not calculated:
            raise ValueError("is_material must be derived from valid approved materiality impacts")
        impact_ids = [impact.impact_id for impact in self.impacts]
        if len(impact_ids) != len(set(impact_ids)):
            raise ValueError("materiality impact IDs must be unique")
        return self


class ReviewFindingV1(StrictContractModel):
    """Immutable, structured finding emitted by the primary reviewer."""

    finding_id: Identifier
    target_artifact_reference: ArtifactReferenceV1
    target_claim_ids: tuple[Identifier, ...] = Field(strict=False)
    json_pointer: Annotated[str, Field(pattern=r"^(?:/(?:[^~/]|~0|~1)*)*$")]
    horizon: Literal["medium_term", "long_term", "cross_horizon", "artifact"]
    summary: NonEmptyString
    detail: NonEmptyString
    primary_classification: FindingClassificationValue | None
    secondary_classifications: tuple[FindingClassificationValue, ...] = Field(strict=False)
    classification_reason: NonEmptyString | None
    classification_pending: ClassificationPendingV1 | None
    severity: FindingSeverityValue
    severity_reason: NonEmptyString
    evidence_ids: tuple[Identifier, ...] = Field(strict=False)
    policy_references: tuple[ArtifactReferenceV1, ...] = Field(strict=False)
    schema_references: tuple[NonEmptyString, ...] = Field(strict=False)
    materiality: MaterialityAssessmentV1
    expected_resolution: NonEmptyString
    lifecycle: FindingLifecycleValue
    unresolved_retention_reason: NonEmptyString | None

    @model_validator(mode="after")
    def validate_classification_and_lifecycle(self) -> ReviewFindingV1:
        """Enforce the terminal-classification and pending-state boundary."""
        pending = self.classification_pending is not None
        classified = self.primary_classification is not None
        if pending is classified:
            raise ValueError("a finding must have either a primary classification or classification_pending")
        if pending:
            if self.secondary_classifications or self.classification_reason is not None:
                raise ValueError("classification_pending findings must not claim finalized classifications")
            if self.lifecycle in {
                FindingLifecycle.RESOLVED,
                FindingLifecycle.DISPUTED,
                FindingLifecycle.HUMAN_DECISION_REQUIRED,
            }:
                raise ValueError("classification_pending findings cannot enter a terminal or escalation lifecycle")
        elif self.classification_reason is None:
            raise ValueError("classified findings require a classification reason")
        if len(self.secondary_classifications) != len(set(self.secondary_classifications)):
            raise ValueError("secondary finding classifications must be unique")
        if self.primary_classification in self.secondary_classifications:
            raise ValueError("the primary classification must not be repeated as a secondary classification")
        if len(self.target_claim_ids) != len(set(self.target_claim_ids)):
            raise ValueError("target claim IDs must be unique")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("finding evidence IDs must be unique")
        unresolved = self.lifecycle is not FindingLifecycle.RESOLVED
        if (
            unresolved
            and self.severity in {FindingSeverity.LOW, FindingSeverity.MEDIUM}
            and self.unresolved_retention_reason is None
        ):
            raise ValueError("retained low or medium findings require an unresolved retention reason")
        if not unresolved and self.unresolved_retention_reason is not None:
            raise ValueError("resolved findings must not contain an unresolved retention reason")
        return self


class ReviewCompletionGateV1(StrictContractModel):
    """Machine-verifiable primary-review completion inputs."""

    structured_output_valid: bool
    evidence_references_valid: bool
    finding_processing_complete: bool
    policy_deviation_records_complete: bool


class PrimaryReviewV1(StrictContractModel):
    """Primary review result with a deterministically checked pass flag."""

    schema_id: Literal["detailed-analysis.primary-review"]
    schema_version: Literal[1]
    task_id: Identifier
    artifact_id: Identifier
    reviewer_agent_run_id: Identifier
    reviewed_synthesis_reference: ArtifactReferenceV1
    previous_review_reference: ArtifactReferenceV1 | None
    reviewed_response_references: tuple[ArtifactReferenceV1, ...] = Field(strict=False)
    evidence_set_version: int = Field(ge=1)
    review_round: int = Field(ge=1)
    created_at: Timestamp
    overall_status: ReviewOverallStatusValue
    findings: tuple[ReviewFindingV1, ...] = Field(strict=False)
    reviewer_rechecks: tuple[ReviewerRecheckV1, ...] = Field(strict=False)
    completion_gate: ReviewCompletionGateV1
    review_passed: bool

    @field_validator("created_at")
    @classmethod
    def ensure_valid_created_at(cls, value: str) -> str:
        """Validate the review timestamp."""
        _parse_rfc3339(value, "created_at")
        return value

    @model_validator(mode="after")
    def validate_review_pass_flag(self) -> PrimaryReviewV1:
        """Block review passage on pending, material, or major unresolved findings."""
        if self.reviewed_synthesis_reference.schema_id != "detailed-analysis.synthesis-result":
            raise ValueError("primary review must reference a synthesis-result artifact")
        if self.previous_review_reference is not None:
            if self.previous_review_reference.schema_id != "detailed-analysis.primary-review":
                raise ValueError("previous review must reference a primary-review artifact")
            if self.review_round == 1:
                raise ValueError("the first review round must not reference a previous review")
        elif self.review_round != 1:
            raise ValueError("later review rounds require a previous primary-review reference")
        if any(
            reference.schema_id != "detailed-analysis.review-response"
            for reference in self.reviewed_response_references
        ):
            raise ValueError("reviewed responses must reference review-response artifacts")
        finding_ids = [finding.finding_id for finding in self.findings]
        if len(finding_ids) != len(set(finding_ids)):
            raise ValueError("finding IDs must be unique within a primary review")
        rechecked_finding_ids = [recheck.finding_id for recheck in self.reviewer_rechecks]
        if len(rechecked_finding_ids) != len(set(rechecked_finding_ids)):
            raise ValueError("a primary review round must not recheck the same finding more than once")
        response_ids = {reference.artifact_id for reference in self.reviewed_response_references}
        if any(recheck.response_reference.artifact_id not in response_ids for recheck in self.reviewer_rechecks):
            raise ValueError("each reviewer recheck must reference a response reviewed in this round")
        resolved_major_ids = {
            finding.finding_id
            for finding in self.findings
            if finding.lifecycle is FindingLifecycle.RESOLVED
            and finding.severity in {FindingSeverity.HIGH, FindingSeverity.CRITICAL}
        }
        confirmed_major_ids = {
            recheck.finding_id
            for recheck in self.reviewer_rechecks
            if recheck.resulting_lifecycle is FindingLifecycle.RESOLVED
            and recheck.decision in {ReReviewDecision.ACCEPTED, ReReviewDecision.FINDING_WITHDRAWN}
        }
        if not resolved_major_ids.issubset(confirmed_major_ids):
            raise ValueError("resolved high or critical findings require a confirming reviewer recheck")
        unresolved_blocker = any(
            finding.classification_pending is not None
            or (
                finding.lifecycle is not FindingLifecycle.RESOLVED
                and (
                    finding.severity in {FindingSeverity.HIGH, FindingSeverity.CRITICAL}
                    or finding.materiality.is_material
                )
            )
            for finding in self.findings
        )
        gates_pass = all(
            (
                self.completion_gate.structured_output_valid,
                self.completion_gate.evidence_references_valid,
                self.completion_gate.finding_processing_complete,
                self.completion_gate.policy_deviation_records_complete,
            )
        )
        status_passes = self.overall_status in {
            ReviewOverallStatus.APPROVE,
            ReviewOverallStatus.APPROVE_WITH_CHANGES,
        }
        calculated = gates_pass and status_passes and not unresolved_blocker
        if self.review_passed is not calculated:
            raise ValueError("review_passed must equal the approved completion-gate calculation")
        return self


class ReviewerRecheckV1(StrictContractModel):
    """Primary reviewer's immutable recheck after one response."""

    recheck_id: Identifier
    finding_id: Identifier
    response_reference: ArtifactReferenceV1
    reviewer_agent_run_id: Identifier
    created_at: Timestamp
    decision: ReReviewDecisionValue
    rationale: NonEmptyString
    resulting_severity: FindingSeverityValue | None
    resulting_lifecycle: FindingLifecycleValue

    @field_validator("created_at")
    @classmethod
    def ensure_valid_created_at(cls, value: str) -> str:
        """Validate the recheck timestamp."""
        _parse_rfc3339(value, "created_at")
        return value

    @model_validator(mode="after")
    def validate_response_reference(self) -> ReviewerRecheckV1:
        """Keep response and reviewer provenance in separate artifacts."""
        if self.response_reference.schema_id != "detailed-analysis.review-response":
            raise ValueError("a reviewer recheck must reference a review-response artifact")
        return self


class ReviewResponseV1(StrictContractModel):
    """One finding response and the optional primary-reviewer recheck."""

    schema_id: Literal["detailed-analysis.review-response"]
    schema_version: Literal[1]
    task_id: Identifier
    artifact_id: Identifier
    response_id: Identifier
    finding_id: Identifier
    finding_severity: FindingSeverityValue
    responder_agent_run_id: Identifier
    responder_role: Literal["codex_worker", "claude_worker", "orchestrator"]
    created_at: Timestamp
    disposition: ResponseDispositionValue
    rationale: NonEmptyString
    evidence_ids: tuple[Identifier, ...] = Field(strict=False)
    policy_references: tuple[ArtifactReferenceV1, ...] = Field(strict=False)
    changed_claim_ids: tuple[Identifier, ...] = Field(strict=False)
    changed_assessments: tuple[Literal["medium_term", "long_term"], ...] = Field(strict=False)
    materiality_objection: MaterialityAssessmentV1 | None
    proposed_lifecycle: FindingLifecycleValue

    @field_validator("created_at")
    @classmethod
    def ensure_valid_created_at(cls, value: str) -> str:
        """Validate the response timestamp."""
        _parse_rfc3339(value, "created_at")
        return value

    @model_validator(mode="after")
    def validate_response_lifecycle(self) -> ReviewResponseV1:
        """Prevent a unilateral rejection from closing a high or critical finding."""
        if (
            self.disposition is ResponseDisposition.REJECTED
            and self.finding_severity in {FindingSeverity.HIGH, FindingSeverity.CRITICAL}
            and self.proposed_lifecycle not in {FindingLifecycle.DISPUTED, FindingLifecycle.HUMAN_DECISION_REQUIRED}
        ):
            raise ValueError("rejected high or critical findings must be disputed or sent to human decision")
        if self.proposed_lifecycle is FindingLifecycle.RESOLVED and self.finding_severity in {
            FindingSeverity.HIGH,
            FindingSeverity.CRITICAL,
        }:
            raise ValueError("a response cannot unilaterally propose resolution for a high or critical finding")
        return self
