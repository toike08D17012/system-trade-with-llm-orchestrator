"""Task state, checkpoint, and execution manifest contracts for version 1."""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from stock_research_llm_orchestrator.contracts.base import (
    ArtifactReferenceV1,
    Identifier,
    NonEmptyString,
    Sha256Hex,
    StrictContractModel,
    Timestamp,
)
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.evidence import _parse_rfc3339


class TaskExecutionState(StrEnum):
    """Approved task-level states without a Finalized state."""

    RUNNING = "running"
    INTERRUPTING = "interrupting"
    SUSPENDED = "suspended"
    AWAITING_HUMAN_DECISION = "awaiting_human_decision"
    ANALYSIS_COMPLETED = "analysis_completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TaskExecutionStateValue = Annotated[TaskExecutionState, Field(strict=False)]
TransitionSource = Literal[
    "initial",
    "running",
    "interrupting",
    "suspended",
    "awaiting_human_decision",
    "analysis_completed",
    "failed",
    "cancelled",
]


ALLOWED_TASK_TRANSITIONS = frozenset(
    {
        ("initial", "running"),
        ("running", "interrupting"),
        ("interrupting", "suspended"),
        ("suspended", "running"),
        ("suspended", "failed"),
        ("suspended", "cancelled"),
        ("running", "awaiting_human_decision"),
        ("awaiting_human_decision", "running"),
        ("awaiting_human_decision", "analysis_completed"),
        ("awaiting_human_decision", "failed"),
        ("awaiting_human_decision", "cancelled"),
        ("running", "analysis_completed"),
        ("running", "failed"),
        ("running", "cancelled"),
    }
)


class TaskStateTransitionV1(StrictContractModel):
    """One immutable task-level transition caused by a logical operation."""

    transition_id: Identifier
    operation_id: Identifier
    from_state: TransitionSource
    to_state: TaskExecutionStateValue
    occurred_at: Timestamp
    reason_code: Identifier
    reason: NonEmptyString

    @field_validator("occurred_at")
    @classmethod
    def ensure_valid_occurred_at(cls, value: str) -> str:
        """Validate the transition timestamp."""
        _parse_rfc3339(value, "occurred_at")
        return value

    @model_validator(mode="after")
    def validate_allowed_transition(self) -> TaskStateTransitionV1:
        """Reject every task-level transition not explicitly approved."""
        if (self.from_state, self.to_state.value) not in ALLOWED_TASK_TRANSITIONS:
            raise ValueError("task state transition is not allowed")
        return self


class ResumeCheckpointV1(StrictContractModel):
    """Exact validated inputs and artifacts required for safe task resume."""

    checkpoint_id: Identifier
    created_at: Timestamp
    input_sha256: Sha256Hex
    evidence_set_reference: ArtifactReferenceV1
    evidence_set_version: int = Field(ge=1)
    policy_references: tuple[ArtifactReferenceV1, ...] = Field(strict=False, min_length=1)
    completed_stage_ids: tuple[Identifier, ...] = Field(strict=False)
    incomplete_stage_ids: tuple[Identifier, ...] = Field(strict=False)
    artifact_references: tuple[ArtifactReferenceV1, ...] = Field(strict=False)
    resume_validation_completed: bool
    source_approvals_revalidated: bool
    source_profiles_revalidated: bool
    credentials_revalidated: bool
    coordinator_state_revalidated: bool
    evidence_freshness_revalidated: bool
    reusable_for_resume: bool

    @field_validator("created_at")
    @classmethod
    def ensure_valid_created_at(cls, value: str) -> str:
        """Validate the checkpoint timestamp."""
        _parse_rfc3339(value, "created_at")
        return value

    @model_validator(mode="after")
    def validate_resume_readiness(self) -> ResumeCheckpointV1:
        """Derive resume readiness from every required revalidation result."""
        checks = (
            self.resume_validation_completed,
            self.source_approvals_revalidated,
            self.source_profiles_revalidated,
            self.credentials_revalidated,
            self.coordinator_state_revalidated,
            self.evidence_freshness_revalidated,
        )
        if self.reusable_for_resume is not all(checks):
            raise ValueError("reusable_for_resume must equal all required resume validation checks")
        if set(self.completed_stage_ids) & set(self.incomplete_stage_ids):
            raise ValueError("checkpoint stages must not be both complete and incomplete")
        if self.evidence_set_reference.schema_id != "detailed-analysis.evidence-set":
            raise ValueError("checkpoint evidence must reference an evidence-set artifact")
        return self


class ManifestArtifactV1(StrictContractModel):
    """Artifact provenance and validation status tracked by the manifest."""

    reference: ArtifactReferenceV1
    producer_role: Identifier
    created_at: Timestamp
    validation_status: Literal["valid", "invalid", "not_validated"]
    reused_from_task_id: Identifier | None
    reuse_revalidated: bool

    @field_validator("created_at")
    @classmethod
    def ensure_valid_created_at(cls, value: str) -> str:
        """Validate the artifact creation timestamp."""
        _parse_rfc3339(value, "created_at")
        return value

    @model_validator(mode="after")
    def validate_reuse(self) -> ManifestArtifactV1:
        """Require revalidation for every cross-task reused artifact."""
        if self.reuse_revalidated is not (self.reused_from_task_id is not None):
            raise ValueError("reuse_revalidated must be true exactly for cross-task reused artifacts")
        return self


class ExecutionManifestV1(StrictContractModel):
    """Task-level source of truth for state and artifact provenance."""

    schema_id: Literal["detailed-analysis.execution-manifest"]
    schema_version: Literal[1]
    task_id: Identifier
    artifact_id: Identifier
    manifest_version: int = Field(ge=1)
    updated_at: Timestamp
    task_reference: ArtifactReferenceV1
    current_state: TaskExecutionStateValue
    state_transitions: tuple[TaskStateTransitionV1, ...] = Field(strict=False, min_length=1)
    current_checkpoint: ResumeCheckpointV1 | None
    current_evidence_set_reference: ArtifactReferenceV1
    current_evidence_set_version: int = Field(ge=1)
    agent_execution_references: tuple[ArtifactReferenceV1, ...] = Field(strict=False)
    instruction_application_references: tuple[ArtifactReferenceV1, ...] = Field(strict=False)
    external_request_event_references: tuple[ArtifactReferenceV1, ...] = Field(strict=False)
    operation_request_references: tuple[ArtifactReferenceV1, ...] = Field(strict=False)
    operation_result_references: tuple[ArtifactReferenceV1, ...] = Field(strict=False)
    artifacts: tuple[ManifestArtifactV1, ...] = Field(strict=False)
    final_analysis_result_reference: ArtifactReferenceV1 | None
    classification_pending_present: bool
    unresolved_high_or_critical_finding_present: bool
    unresolved_material_dispute_present: bool
    primary_review_passed: bool
    human_decision_required: bool
    automatic_retry_allowed: Literal[False]

    @field_validator("updated_at")
    @classmethod
    def ensure_valid_updated_at(cls, value: str) -> str:
        """Validate the manifest update timestamp."""
        _parse_rfc3339(value, "updated_at")
        return value

    @model_validator(mode="after")
    def validate_manifest(self) -> ExecutionManifestV1:
        """Validate contiguous history and all analysis-completion blockers."""
        if self.task_reference.schema_id != "detailed-analysis.detailed-analysis-task":
            raise ValueError("manifest task_reference must use the detailed-analysis-task schema")
        if self.current_evidence_set_reference.schema_id != "detailed-analysis.evidence-set":
            raise ValueError("manifest evidence reference must use the evidence-set schema")
        reference_groups = (
            (self.agent_execution_references, "detailed-analysis.agent-execution"),
            (self.instruction_application_references, "detailed-analysis.instruction-application"),
            (self.external_request_event_references, "detailed-analysis.external-request-event"),
            (self.operation_request_references, "detailed-analysis.operation-request"),
            (self.operation_result_references, "detailed-analysis.operation-result"),
        )
        for references, expected_schema in reference_groups:
            if any(reference.schema_id != expected_schema for reference in references):
                raise ValueError("manifest execution references must use their exact public schemas")
        expected_source = "initial"
        previous_time = None
        transition_ids: set[str] = set()
        for transition in self.state_transitions:
            if transition.transition_id in transition_ids:
                raise ValueError("manifest transition IDs must be unique")
            transition_ids.add(transition.transition_id)
            if transition.from_state != expected_source:
                raise ValueError("manifest state transitions must form one contiguous history")
            occurred_at = _parse_rfc3339(transition.occurred_at, "state_transitions.occurred_at")
            if previous_time is not None and occurred_at < previous_time:
                raise ValueError("manifest state transitions must be chronological")
            previous_time = occurred_at
            expected_source = transition.to_state.value
        if expected_source != self.current_state.value:
            raise ValueError("current_state must equal the final manifest transition target")
        if self.current_state is TaskExecutionState.SUSPENDED:
            if self.current_checkpoint is None:
                raise ValueError("suspended tasks require a resume checkpoint")
        elif self.current_checkpoint is not None and self.current_checkpoint.reusable_for_resume:
            raise ValueError("only a suspended task may expose a reusable resume checkpoint")
        if self.current_state is TaskExecutionState.AWAITING_HUMAN_DECISION and not self.human_decision_required:
            raise ValueError("awaiting_human_decision requires the human decision flag")
        if self.current_state is TaskExecutionState.ANALYSIS_COMPLETED:
            blockers = (
                self.classification_pending_present,
                self.unresolved_high_or_critical_finding_present,
                self.unresolved_material_dispute_present,
                self.human_decision_required,
            )
            if any(blockers) or not self.primary_review_passed:
                raise ValueError("analysis_completed is blocked by unresolved review, dispute, or human-decision state")
            if self.final_analysis_result_reference is None:
                raise ValueError("analysis_completed requires a final-analysis-result reference")
            if self.final_analysis_result_reference.schema_id != "detailed-analysis.final-analysis-result":
                raise ValueError("completed manifest must reference the final-analysis-result schema")
        elif self.final_analysis_result_reference is not None:
            raise ValueError("only analysis_completed may adopt a final-analysis-result reference")
        return self
