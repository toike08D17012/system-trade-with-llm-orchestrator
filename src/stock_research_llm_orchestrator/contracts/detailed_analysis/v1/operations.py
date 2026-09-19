"""Logical CLI operation request and result contracts for version 1."""

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
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.agent_execution import AgentUsageMetricV1
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.evidence import _parse_rfc3339
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.runtime import TaskExecutionStateValue


class OperationType(StrEnum):
    """Required logical operations independent of concrete CLI spelling."""

    VALIDATE_TASK = "validate_task"
    START = "start"
    PREFLIGHT = "preflight"
    STATUS = "status"
    INTERRUPT = "interrupt"
    CANCEL = "cancel"
    RESUME = "resume"
    RERUN = "rerun"
    RECORD_HUMAN_DECISION = "record_human_decision"
    SHOW_ARTIFACT = "show_artifact"


class OperationStatus(StrEnum):
    """Outcome of one logical CLI operation."""

    ACCEPTED = "accepted"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


OperationTypeValue = Annotated[OperationType, Field(strict=False)]
OperationStatusValue = Annotated[OperationStatus, Field(strict=False)]


class OperationRequestV1(StrictContractModel):
    """Auditable logical operation input without secret credential values."""

    schema_id: Literal["detailed-analysis.operation-request"]
    schema_version: Literal[1]
    operation_id: Identifier
    operation_type: OperationTypeValue
    requested_at: Timestamp
    operator: NonEmptyString
    task_id: Identifier | None
    parent_task_id: Identifier | None
    task_reference: ArtifactReferenceV1 | None
    manifest_reference: ArtifactReferenceV1 | None
    expected_task_state: TaskExecutionStateValue | None
    input_references: tuple[ArtifactReferenceV1, ...] = Field(strict=False)
    credential_aliases: tuple[Identifier, ...] = Field(strict=False)
    human_decision_reference: ArtifactReferenceV1 | None
    reason: NonEmptyString
    non_interactive_inputs_complete: Literal[True]

    @field_validator("requested_at")
    @classmethod
    def ensure_valid_requested_at(cls, value: str) -> str:
        """Validate the request timestamp."""
        _parse_rfc3339(value, "requested_at")
        return value

    @model_validator(mode="after")
    def validate_operation_inputs(self) -> OperationRequestV1:
        """Require explicit task and state inputs for each operation category."""
        new_task_operations = {OperationType.START, OperationType.RERUN}
        taskless_operations = {OperationType.VALIDATE_TASK, OperationType.PREFLIGHT}
        if self.operation_type in new_task_operations:
            if self.task_id is not None:
                raise ValueError("start and rerun requests must not preassign the new task_id")
            if self.task_reference is None:
                raise ValueError("start and rerun requests require a validated task input reference")
        elif self.operation_type not in taskless_operations and self.task_id is None:
            raise ValueError("this logical operation requires an existing task_id")
        if self.operation_type is OperationType.RERUN and self.parent_task_id is None:
            raise ValueError("rerun requests require a parent task_id")
        if self.operation_type is not OperationType.RERUN and self.parent_task_id is not None:
            raise ValueError("only rerun requests may reference a parent task")
        mutating_existing = {
            OperationType.INTERRUPT,
            OperationType.CANCEL,
            OperationType.RESUME,
            OperationType.RECORD_HUMAN_DECISION,
        }
        if self.operation_type in mutating_existing and (
            self.manifest_reference is None or self.expected_task_state is None
        ):
            raise ValueError("mutating operations require manifest and expected state references")
        if self.operation_type is OperationType.RECORD_HUMAN_DECISION:
            if self.human_decision_reference is None:
                raise ValueError("human-decision operations require a human-decision reference")
        elif self.human_decision_reference is not None:
            raise ValueError("only a human-decision operation may include a human-decision reference")
        if len(self.credential_aliases) != len(set(self.credential_aliases)):
            raise ValueError("credential aliases must be unique")
        return self


class OperationErrorV1(StrictContractModel):
    """Safe logical operation failure without embedded secret values."""

    error_code: Identifier
    message: NonEmptyString
    validation_error_reference: ArtifactReferenceV1 | None
    retryable_automatically: Literal[False]
    secret_values_redacted: Literal[True]


class OperationResultV1(StrictContractModel):
    """One-to-one logical operation result for CLI and manifest consumers."""

    schema_id: Literal["detailed-analysis.operation-result"]
    schema_version: Literal[1]
    operation_id: Identifier
    operation_type: OperationTypeValue
    completed_at: Timestamp
    status: OperationStatusValue
    task_id: Identifier | None
    previous_task_state: TaskExecutionStateValue | None
    resulting_task_state: TaskExecutionStateValue | None
    exit_code: int
    generated_artifact_references: tuple[ArtifactReferenceV1, ...] = Field(strict=False)
    reused_artifact_references: tuple[ArtifactReferenceV1, ...] = Field(strict=False)
    manifest_reference: ArtifactReferenceV1 | None
    usage: tuple[AgentUsageMetricV1, ...] = Field(strict=False)
    error: OperationErrorV1 | None
    automatic_retry_allowed: Literal[False]

    @field_validator("completed_at")
    @classmethod
    def ensure_valid_completed_at(cls, value: str) -> str:
        """Validate the result timestamp."""
        _parse_rfc3339(value, "completed_at")
        return value

    @model_validator(mode="after")
    def validate_result(self) -> OperationResultV1:
        """Keep success, failure, state, and error fields internally consistent."""
        successful = self.status in {OperationStatus.ACCEPTED, OperationStatus.SUCCEEDED}
        if successful and self.error is not None:
            raise ValueError("successful operations must not contain an error")
        if not successful and self.error is None:
            raise ValueError("failed, rejected, or cancelled operations require an error")
        if successful and self.exit_code != 0:
            raise ValueError("successful operations require exit_code 0")
        if not successful and self.exit_code == 0:
            raise ValueError("unsuccessful operations require a non-zero exit_code")
        if (self.previous_task_state is None) is not (self.resulting_task_state is None):
            raise ValueError("operation results must contain both task states or neither")
        metrics = [item.metric for item in self.usage]
        if len(metrics) != len(set(metrics)):
            raise ValueError("operation usage metrics must be unique")
        return self
