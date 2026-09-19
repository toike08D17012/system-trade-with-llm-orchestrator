"""External request coordination audit contract for version 1."""

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
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.research import AgentRoleValue


class ExternalRequestEventType(StrEnum):
    """Auditable lifecycle events emitted by the request coordinator."""

    LOGICAL_REQUESTED = "logical_requested"
    CACHE_DECIDED = "cache_decided"
    SINGLE_FLIGHT_DECIDED = "single_flight_decided"
    QUEUED = "queued"
    GATES_EVALUATED = "gates_evaluated"
    PHYSICAL_STARTED = "physical_started"
    PHYSICAL_SUCCEEDED = "physical_succeeded"
    PHYSICAL_FAILED = "physical_failed"
    SESSION_STARTED = "session_started"
    COOLDOWN_RECORDED = "cooldown_recorded"
    CANCELLED = "cancelled"


class RequestObservability(StrEnum):
    """Whether an individual provider request can be observed and controlled."""

    PHYSICAL_REQUEST = "physical_request"
    AGENT_SESSION_ONLY = "agent_session_only"


class GateScope(StrEnum):
    """Approved request gate scopes evaluated immediately before sending."""

    GLOBAL = "global"
    EGRESS = "egress"
    PROVIDER = "provider"
    ORIGIN = "origin"
    CREDENTIAL = "credential"
    OPERATION = "operation"
    TASK = "task"
    ROLE = "role"


class GateOutcome(StrEnum):
    """Outcome of evaluating one applicable rate gate."""

    ACQUIRED = "acquired"
    BLOCKED = "blocked"
    COOLDOWN = "cooldown"


class CacheDecision(StrEnum):
    """Cache decision made before queue and rate gates."""

    NOT_APPLICABLE = "not_applicable"
    MISS = "miss"
    HIT = "hit"
    STALE_REJECTED = "stale_rejected"
    POLICY_REJECTED = "policy_rejected"


class SingleFlightDecision(StrEnum):
    """Single-flight relationship for the logical request."""

    NOT_APPLICABLE = "not_applicable"
    LEADER = "leader"
    FOLLOWER = "follower"


class RequestOutcome(StrEnum):
    """Known outcome at the time an event is written."""

    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class UsageAvailability(StrEnum):
    """Distinguish a measured value from an unavailable zero-like value."""

    RETRIEVED = "retrieved"
    UNKNOWN = "unknown"
    UNSUPPORTED = "unsupported"
    NOT_RETRIEVED = "not_retrieved"


EventTypeValue = Annotated[ExternalRequestEventType, Field(strict=False)]
ObservabilityValue = Annotated[RequestObservability, Field(strict=False)]
GateScopeValue = Annotated[GateScope, Field(strict=False)]
GateOutcomeValue = Annotated[GateOutcome, Field(strict=False)]
CacheDecisionValue = Annotated[CacheDecision, Field(strict=False)]
SingleFlightDecisionValue = Annotated[SingleFlightDecision, Field(strict=False)]
RequestOutcomeValue = Annotated[RequestOutcome, Field(strict=False)]
UsageAvailabilityValue = Annotated[UsageAvailability, Field(strict=False)]


class RateGateResultV1(StrictContractModel):
    """Non-secret result for one applicable coordinator gate."""

    scope: GateScopeValue
    key_alias: Identifier
    outcome: GateOutcomeValue
    waited_ms: int = Field(ge=0)
    detail: NonEmptyString


class RequestUsageV1(StrictContractModel):
    """One usage metric with explicit availability semantics."""

    metric: Identifier
    unit: Identifier
    availability: UsageAvailabilityValue
    value: int | float | None

    @model_validator(mode="after")
    def validate_availability(self) -> RequestUsageV1:
        """Forbid substituting zero when a provider did not return usage."""
        if self.availability is UsageAvailability.RETRIEVED and self.value is None:
            raise ValueError("retrieved usage requires a value")
        if self.availability is not UsageAvailability.RETRIEVED and self.value is not None:
            raise ValueError("unavailable usage must have a null value")
        if self.value is not None and self.value < 0:
            raise ValueError("usage values must not be negative")
        return self


class RequestErrorV1(StrictContractModel):
    """Provider or coordination failure without secret response content."""

    error_class: Identifier
    message: NonEmptyString
    retry_after_seconds: float | None = Field(ge=0)
    cooldown_until: Timestamp | None
    response_status_code: int | None = Field(ge=100, le=599)

    @field_validator("cooldown_until")
    @classmethod
    def ensure_valid_cooldown_until(cls, value: str | None) -> str | None:
        """Validate provider cooldown timestamp when present."""
        if value is not None:
            _parse_rfc3339(value, "cooldown_until")
        return value


class ExternalRequestEventV1(StrictContractModel):
    """Immutable coordinator event linking logical consumers and physical work."""

    schema_id: Literal["detailed-analysis.external-request-event"]
    schema_version: Literal[1]
    event_id: Identifier
    occurred_at: Timestamp
    event_type: EventTypeValue
    task_id: Identifier
    logical_request_id: Identifier
    physical_attempt_id: Identifier | None
    observability: ObservabilityValue
    consumer_role: AgentRoleValue
    provider: Identifier
    operation: Identifier
    origin: NonEmptyString
    credential_scope_alias: Identifier | None
    source_approval_reference: ArtifactReferenceV1
    source_profile_reference: ArtifactReferenceV1
    request_fingerprint: Sha256Hex
    cache_decision: CacheDecisionValue
    cache_artifact_reference: ArtifactReferenceV1 | None
    single_flight_decision: SingleFlightDecisionValue
    leader_logical_request_id: Identifier | None
    queue_wait_ms: int = Field(ge=0)
    gate_results: tuple[RateGateResultV1, ...] = Field(strict=False)
    outcome: RequestOutcomeValue
    error: RequestErrorV1 | None
    usage: tuple[RequestUsageV1, ...] = Field(strict=False)
    automatic_retry_allowed: Literal[False]
    direct_connection_fallback_allowed: Literal[False]

    @field_validator("occurred_at")
    @classmethod
    def ensure_valid_occurred_at(cls, value: str) -> str:
        """Validate the audit event timestamp."""
        _parse_rfc3339(value, "occurred_at")
        return value

    @model_validator(mode="after")
    def validate_request_event(self) -> ExternalRequestEventV1:
        """Enforce observability, cache, single-flight, and failure boundaries."""
        if self.source_approval_reference.schema_id != "detailed-analysis.source-approval":
            raise ValueError("source_approval_reference must use the source-approval schema")
        if self.source_profile_reference.schema_id != "detailed-analysis.source-profile":
            raise ValueError("source_profile_reference must use the source-profile schema")

        physical_events = {
            ExternalRequestEventType.PHYSICAL_STARTED,
            ExternalRequestEventType.PHYSICAL_SUCCEEDED,
            ExternalRequestEventType.PHYSICAL_FAILED,
            ExternalRequestEventType.COOLDOWN_RECORDED,
        }
        if self.observability is RequestObservability.PHYSICAL_REQUEST:
            if self.event_type in physical_events and self.physical_attempt_id is None:
                raise ValueError("physical request events require physical_attempt_id")
        else:
            if self.physical_attempt_id is not None:
                raise ValueError("agent-session-only events must not claim a physical attempt")
            if any(result.scope is GateScope.ORIGIN for result in self.gate_results):
                raise ValueError("unobservable agent requests must not claim origin-level control")

        if self.cache_decision is CacheDecision.HIT and self.cache_artifact_reference is None:
            raise ValueError("cache hits require an exact cache artifact reference")
        if self.cache_decision is not CacheDecision.HIT and self.cache_artifact_reference is not None:
            raise ValueError("only cache hits may reference a cache artifact")
        if self.single_flight_decision is SingleFlightDecision.FOLLOWER:
            if self.leader_logical_request_id is None:
                raise ValueError("single-flight followers require the leader logical request ID")
        elif self.leader_logical_request_id is not None:
            raise ValueError("only single-flight followers may reference a leader")

        if self.outcome is RequestOutcome.FAILED and self.error is None:
            raise ValueError("failed requests require a structured error")
        if self.outcome is not RequestOutcome.FAILED and self.error is not None:
            raise ValueError("only failed requests may contain an error")
        usage_metrics = [item.metric for item in self.usage]
        if len(usage_metrics) != len(set(usage_metrics)):
            raise ValueError("usage metrics must be unique within an event")
        return self
