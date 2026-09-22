"""Internal production request state persisted before external transport."""

from enum import StrEnum
from typing import Annotated

from pydantic import Field, ValidationInfo, field_validator, model_validator

from stock_research_llm_orchestrator.contracts.base import Identifier, Sha256Hex, StrictContractModel, Timestamp
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.evidence import _parse_rfc3339


class LogicalRequestState(StrEnum):
    """Durable lifecycle state for one logical external request."""

    QUEUED = "queued"
    CANCELLED = "cancelled"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class PhysicalAttemptState(StrEnum):
    """Durable lifecycle state for one controlled physical attempt."""

    RESERVED = "reserved"
    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"


class LogicalResultOutcome(StrEnum):
    """Terminal outcome for exactly one logical request result."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


LogicalRequestStateValue = Annotated[LogicalRequestState, Field(strict=False)]
PhysicalAttemptStateValue = Annotated[PhysicalAttemptState, Field(strict=False)]
LogicalResultOutcomeValue = Annotated[LogicalResultOutcome, Field(strict=False)]


class ProductionLogicalRequest(StrictContractModel):
    """Secret-free logical request persisted before queue processing."""

    logical_request_id: Identifier
    task_id: Identifier
    source_id: Identifier
    operation: Identifier
    request_fingerprint: Sha256Hex
    source_approval_version: int = Field(ge=1)
    source_profile_version: int = Field(ge=1)
    credential_scope_alias: Identifier | None
    egress_scope: Identifier
    created_at: Timestamp
    state: LogicalRequestStateValue = LogicalRequestState.QUEUED

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: str) -> str:
        """Require a semantic RFC 3339 timestamp."""
        _parse_rfc3339(value, "created_at")
        return value


class ProductionPhysicalAttempt(StrictContractModel):
    """Secret-free physical attempt reserved under one lease generation."""

    physical_attempt_id: Identifier
    logical_request_id: Identifier
    sequence_number: int = Field(ge=1)
    lease_generation: int = Field(ge=1)
    created_at: Timestamp
    state: PhysicalAttemptStateValue = PhysicalAttemptState.RESERVED

    @field_validator("created_at")
    @classmethod
    def validate_created_at(cls, value: str) -> str:
        """Require a semantic RFC 3339 timestamp."""
        _parse_rfc3339(value, "created_at")
        return value

    @model_validator(mode="after")
    def forbid_terminal_initial_state(self) -> ProductionPhysicalAttempt:
        """Allow only a reserved attempt to be newly persisted."""
        if self.state is not PhysicalAttemptState.RESERVED:
            raise ValueError("new physical attempts must be reserved")
        return self


class ProductionLogicalResult(StrictContractModel):
    """Sanitized terminal result persisted without response content."""

    logical_request_id: Identifier
    outcome: LogicalResultOutcomeValue
    completed_at: Timestamp
    error_code: Identifier | None

    @field_validator("completed_at")
    @classmethod
    def validate_completed_at(cls, value: str) -> str:
        """Require a semantic RFC 3339 timestamp."""
        _parse_rfc3339(value, "completed_at")
        return value

    @model_validator(mode="after")
    def validate_error_code(self) -> ProductionLogicalResult:
        """Keep failure classification explicit and content-free."""
        if self.outcome in {LogicalResultOutcome.FAILED, LogicalResultOutcome.UNKNOWN}:
            if self.error_code is None:
                raise ValueError("failed or unknown results require error_code")
        elif self.error_code is not None:
            raise ValueError("only failed or unknown results may contain error_code")
        return self


class RuntimeLeasePolicy(StrictContractModel):
    """Versioned timing policy for one production runtime owner."""

    lease_duration_seconds: int = Field(default=30, ge=3)
    heartbeat_interval_seconds: int = Field(default=10, ge=1)

    @model_validator(mode="after")
    def validate_heartbeat_interval(self) -> RuntimeLeasePolicy:
        """Require heartbeat at least three times within one lease."""
        if self.heartbeat_interval_seconds * 3 > self.lease_duration_seconds:
            raise ValueError("heartbeat interval must not exceed one third of lease duration")
        return self


class RuntimeLease(StrictContractModel):
    """Current fenced ownership of the production request runtime."""

    owner_token: Identifier
    generation: int = Field(ge=1)
    acquired_at: Timestamp
    heartbeat_at: Timestamp
    expires_at: Timestamp

    @field_validator("acquired_at", "heartbeat_at", "expires_at")
    @classmethod
    def validate_timestamp(cls, value: str, info: ValidationInfo) -> str:
        """Require semantic RFC 3339 timestamps for lease state."""
        field_name = info.field_name or "lease_timestamp"
        _parse_rfc3339(value, field_name)
        return value


class QueuePolicy(StrictContractModel):
    """Versioned backpressure limits for the production queue."""

    global_limit: int = Field(default=1024, ge=1)
    rate_domain_limit: int = Field(default=256, ge=1)
    task_rate_domain_limit: int = Field(default=64, ge=1)

    @model_validator(mode="after")
    def validate_limit_hierarchy(self) -> QueuePolicy:
        """Reject internally contradictory queue limits."""
        if self.task_rate_domain_limit > self.rate_domain_limit:
            raise ValueError("task rate-domain limit must not exceed rate-domain limit")
        if self.rate_domain_limit > self.global_limit:
            raise ValueError("rate-domain limit must not exceed global limit")
        return self


class QueueClaim(StrictContractModel):
    """One durable queue item selected by the fair scheduler."""

    logical_request_id: Identifier
    task_id: Identifier
    rate_domain: Identifier
    enqueued_at: Timestamp

    @field_validator("enqueued_at")
    @classmethod
    def validate_enqueued_at(cls, value: str) -> str:
        """Require a semantic RFC 3339 queue timestamp."""
        _parse_rfc3339(value, "enqueued_at")
        return value
