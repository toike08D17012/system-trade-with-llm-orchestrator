"""Agent session and per-run execution contract for version 1."""

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
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.external_requests import UsageAvailabilityValue
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.research import AgentRoleValue


class AgentProcessState(StrEnum):
    """Process state distinct from the task-level workflow state."""

    QUEUED = "queued"
    RUNNING = "running"
    CANCELLING = "cancelling"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class SessionMode(StrEnum):
    """How the logical session and provider context were started."""

    NEW = "new"
    RESUME = "resume"
    NATIVE_FORK = "native_fork"
    RECONSTRUCTED_FORK = "reconstructed_fork"


class UsageOrigin(StrEnum):
    """Whether a usage value was provider-reported or locally derived."""

    PROVIDER_REPORTED = "provider_reported"
    DERIVED = "derived"
    NOT_AVAILABLE = "not_available"


class UsageScope(StrEnum):
    """Aggregation boundary communicated by the provider or derivation."""

    RUN = "run"
    SESSION_CUMULATIVE = "session_cumulative"
    UNKNOWN = "unknown"


AgentProcessStateValue = Annotated[AgentProcessState, Field(strict=False)]
SessionModeValue = Annotated[SessionMode, Field(strict=False)]
UsageOriginValue = Annotated[UsageOrigin, Field(strict=False)]
UsageScopeValue = Annotated[UsageScope, Field(strict=False)]


class LogicalSessionSettingsV1(StrictContractModel):
    """Settings that remain immutable throughout one logical session."""

    provider: Identifier
    model: NonEmptyString
    effort: NonEmptyString
    role: AgentRoleValue
    role_instruction_reference: ArtifactReferenceV1
    permission_profile: Identifier
    workspace_profile: Identifier
    web_research_policy_reference: ArtifactReferenceV1
    external_transfer_policy_reference: ArtifactReferenceV1
    output_schema_id: NonEmptyString
    output_schema_version: int = Field(ge=1)
    output_schema_sha256: Sha256Hex
    settings_sha256: Sha256Hex

    @model_validator(mode="after")
    def validate_policy_references(self) -> LogicalSessionSettingsV1:
        """Bind the web policy to its public contract without guessing provider details."""
        if self.web_research_policy_reference.schema_id != "detailed-analysis.web-research-policy":
            raise ValueError("web research policy must use the web-research-policy schema")
        return self


class AgentUsageMetricV1(StrictContractModel):
    """One usage value with source, scope, and unavailable-state semantics."""

    metric: Identifier
    unit: Identifier
    availability: UsageAvailabilityValue
    origin: UsageOriginValue
    scope: UsageScopeValue
    meaning_confirmed: bool
    value: int | float | None

    @model_validator(mode="after")
    def validate_usage(self) -> AgentUsageMetricV1:
        """Never replace unavailable or meaning-unconfirmed values with zero."""
        if self.availability.value == "retrieved":
            if self.value is None:
                raise ValueError("retrieved Agent usage requires a value")
            if self.origin is UsageOrigin.NOT_AVAILABLE:
                raise ValueError("retrieved Agent usage requires a reported or derived origin")
        else:
            if self.value is not None:
                raise ValueError("unavailable Agent usage must have a null value")
            if self.origin is not UsageOrigin.NOT_AVAILABLE:
                raise ValueError("unavailable Agent usage must use the not_available origin")
        if not self.meaning_confirmed and self.value is not None:
            raise ValueError("meaning-unconfirmed Agent usage must not contain a numeric value")
        if self.value is not None and self.value < 0:
            raise ValueError("Agent usage values must not be negative")
        return self


class AgentExecutionErrorV1(StrictContractModel):
    """Redacted process or provider error retained for safe recovery."""

    error_class: Identifier
    message: NonEmptyString
    exit_code: int | None
    stdout_reference: ArtifactReferenceV1 | None
    stderr_reference: ArtifactReferenceV1 | None
    unresolved_items: tuple[NonEmptyString, ...] = Field(strict=False)
    secret_values_redacted: Literal[True]


class AgentExecutionV1(StrictContractModel):
    """One Agent CLI run and its relationship to a logical session."""

    schema_id: Literal["detailed-analysis.agent-execution"]
    schema_version: Literal[1]
    task_id: Identifier
    artifact_id: Identifier
    logical_session_id: Identifier
    provider_session_id: NonEmptyString | None
    agent_run_id: Identifier
    parent_agent_run_id: Identifier | None
    parent_logical_session_id: Identifier | None
    role: AgentRoleValue
    logical_agent: Identifier
    purpose: Identifier
    session_mode: SessionModeValue
    session_mode_reason_code: Identifier
    session_mode_reason: NonEmptyString
    settings: LogicalSessionSettingsV1
    process_state: AgentProcessStateValue
    queued_at: Timestamp
    started_at: Timestamp | None
    ended_at: Timestamp | None
    exit_code: int | None
    input_references: tuple[ArtifactReferenceV1, ...] = Field(strict=False, min_length=1)
    output_reference: ArtifactReferenceV1 | None
    raw_output_reference: ArtifactReferenceV1 | None
    validation_error_reference: ArtifactReferenceV1 | None
    error: AgentExecutionErrorV1 | None
    usage: tuple[AgentUsageMetricV1, ...] = Field(strict=False)
    cancellation_requested: bool
    active_run: bool
    automatic_retry_allowed: Literal[False]
    automatic_session_fallback_allowed: Literal[False]

    @field_validator("queued_at", "started_at", "ended_at")
    @classmethod
    def ensure_valid_timestamps(cls, value: str | None, info: object) -> str | None:
        """Validate all known process lifecycle timestamps."""
        if value is not None:
            _parse_rfc3339(value, getattr(info, "field_name", "timestamp"))
        return value

    @model_validator(mode="after")
    def validate_execution(self) -> AgentExecutionV1:
        """Enforce session lineage, process lifecycle, and result boundaries."""
        if self.settings.role is not self.role:
            raise ValueError("Agent execution role must match immutable logical-session settings")
        if self.session_mode is SessionMode.NEW:
            if self.parent_agent_run_id is not None or self.parent_logical_session_id is not None:
                raise ValueError("new sessions must not reference a parent run or logical session")
        elif self.session_mode is SessionMode.RESUME:
            if self.parent_agent_run_id is None or self.parent_logical_session_id is not None:
                raise ValueError("resume requires a parent run in the same logical session")
        elif self.parent_agent_run_id is None or self.parent_logical_session_id is None:
            raise ValueError("fork modes require parent run and logical-session IDs")

        queued_at = _parse_rfc3339(self.queued_at, "queued_at")
        started_at = _parse_rfc3339(self.started_at, "started_at") if self.started_at is not None else None
        ended_at = _parse_rfc3339(self.ended_at, "ended_at") if self.ended_at is not None else None
        if started_at is not None and started_at < queued_at:
            raise ValueError("Agent execution cannot start before it is queued")
        if ended_at is not None and (started_at is None or ended_at < started_at):
            raise ValueError("Agent execution cannot end before it starts")

        terminal = self.process_state in {
            AgentProcessState.SUCCEEDED,
            AgentProcessState.FAILED,
            AgentProcessState.CANCELLED,
        }
        if self.active_run is terminal:
            raise ValueError("active_run must be true exactly for non-terminal process states")
        if terminal and self.ended_at is None:
            raise ValueError("terminal Agent executions require ended_at")
        if not terminal and self.ended_at is not None:
            raise ValueError("non-terminal Agent executions must not contain ended_at")
        if self.process_state is AgentProcessState.SUCCEEDED:
            if self.output_reference is None or self.error is not None:
                raise ValueError("succeeded Agent executions require output and no error")
        elif self.output_reference is not None:
            raise ValueError("only succeeded Agent executions may contain an adopted output")
        if self.process_state is AgentProcessState.FAILED and self.error is None:
            raise ValueError("failed Agent executions require a structured error")
        if self.process_state is not AgentProcessState.FAILED and self.error is not None:
            raise ValueError("only failed Agent executions may contain a structured error")
        metrics = [item.metric for item in self.usage]
        if len(metrics) != len(set(metrics)):
            raise ValueError("Agent usage metrics must be unique within a run")
        return self


def validate_agent_execution_history(executions: tuple[AgentExecutionV1, ...]) -> None:
    """Validate serial execution and immutable settings across logical sessions."""
    active_sessions: set[str] = set()
    settings_by_session: dict[str, str] = {}
    role_by_provider_session: dict[tuple[str, str], object] = {}
    run_ids: set[str] = set()
    for execution in executions:
        if execution.agent_run_id in run_ids:
            raise ValueError("agent_run_id must be unique in execution history")
        run_ids.add(execution.agent_run_id)
        settings_hash = settings_by_session.setdefault(execution.logical_session_id, execution.settings.settings_sha256)
        if settings_hash != execution.settings.settings_sha256:
            raise ValueError("logical-session settings must not change across resume runs")
        if execution.active_run:
            if execution.logical_session_id in active_sessions:
                raise ValueError("a logical session can have at most one active run")
            active_sessions.add(execution.logical_session_id)
        if execution.provider_session_id is not None:
            key = (execution.settings.provider, execution.provider_session_id)
            existing_role = role_by_provider_session.setdefault(key, execution.role)
            if existing_role != execution.role:
                raise ValueError("a provider session must not be resumed across roles")
        if execution.session_mode is SessionMode.RESUME and execution.parent_agent_run_id not in run_ids:
            raise ValueError("a resume run must follow its parent run in history")
