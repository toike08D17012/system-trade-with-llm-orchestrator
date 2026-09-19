"""Policy, market, and source configuration contracts for version 1."""

from datetime import date
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
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.research import AgentRoleValue
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.review import (
    FindingClassification,
    FindingClassificationValue,
    MaterialityImpactType,
    MaterialityImpactTypeValue,
)


class ApprovalStatus(StrEnum):
    """Lifecycle state of a source approval record."""

    DRAFT = "draft"
    APPROVED = "approved"
    SUSPENDED = "suspended"
    EXPIRED = "expired"
    REJECTED = "rejected"


class KnowledgeStatus(StrEnum):
    """Whether an external condition was verified rather than assumed."""

    KNOWN = "known"
    UNKNOWN = "unknown"
    UNSUPPORTED = "unsupported"
    NOT_RETRIEVED = "not_retrieved"


class SourceConditionCategory(StrEnum):
    """External conditions required before a source can be enabled."""

    TERMS = "terms"
    PRICING = "pricing"
    AUTHENTICATION = "authentication"
    REDISTRIBUTION = "redistribution"
    DERIVED_OUTPUT = "derived_output"
    EXTERNAL_PROCESSING = "external_processing"
    PROVIDER_RETENTION = "provider_retention"
    PROVIDER_TRAINING = "provider_training"
    FRESHNESS = "freshness"
    AVAILABLE_PERIOD = "available_period"
    RATE_LIMIT = "rate_limit"
    FAILURE_BEHAVIOR = "failure_behavior"


ApprovalStatusValue = Annotated[ApprovalStatus, Field(strict=False)]
KnowledgeStatusValue = Annotated[KnowledgeStatus, Field(strict=False)]
SourceConditionCategoryValue = Annotated[SourceConditionCategory, Field(strict=False)]


def _validate_iso_date(value: str, field_name: str) -> date:
    """Parse an ISO calendar date for policy ordering checks."""
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must use ISO 8601 YYYY-MM-DD") from exc


class EvaluationHorizonPolicyV1(StrictContractModel):
    """Approved month range for one investment evaluation horizon."""

    horizon: Literal["medium_term", "long_term"]
    minimum_months: int = Field(ge=1)
    maximum_months: int = Field(ge=1)
    maximum_inclusive: bool

    @model_validator(mode="after")
    def ensure_month_order(self) -> EvaluationHorizonPolicyV1:
        """Require a non-empty month interval."""
        if self.maximum_months < self.minimum_months:
            raise ValueError("horizon maximum must not precede its minimum")
        return self


class SystemLimitPolicyV1(StrictContractModel):
    """Approved absence of system-defined fixed execution limits."""

    task_timeout_seconds: Literal[None]
    agent_timeout_seconds: Literal[None]
    token_limit: Literal[None]
    external_request_limit: Literal[None]
    cost_limit: Literal[None]
    search_iteration_limit: Literal[None]
    review_round_limit: Literal[None]


class DetailedAnalysisPolicyV1(StrictContractModel):
    """Initial market, horizon, completion, and safety policy."""

    schema_id: Literal["detailed-analysis.detailed-analysis-policy"]
    schema_version: Literal[1]
    artifact_id: Identifier
    policy_version: int = Field(ge=1)
    effective_at: Timestamp
    market_mic: Literal["XTKS"]
    market_timezone: Literal["Asia/Tokyo"]
    instrument_scope: Literal["domestic_cash_equity"]
    horizons: tuple[EvaluationHorizonPolicyV1, ...] = Field(strict=False, min_length=2, max_length=2)
    completion_criteria: tuple[NonEmptyString, ...] = Field(strict=False, min_length=1)
    system_limits: SystemLimitPolicyV1
    brokerage_connection_allowed: Literal[False]
    order_submission_allowed: Literal[False]
    automated_trading_allowed: Literal[False]
    automated_final_investment_decision_allowed: Literal[False]

    @field_validator("effective_at")
    @classmethod
    def ensure_valid_effective_at(cls, value: str) -> str:
        """Validate the policy effective timestamp."""
        _parse_rfc3339(value, "effective_at")
        return value

    @model_validator(mode="after")
    def validate_horizons(self) -> DetailedAnalysisPolicyV1:
        """Fix the approved medium- and long-term ranges in stable order."""
        expected = (
            ("medium_term", 12, 24, False),
            ("long_term", 24, 60, True),
        )
        actual = tuple(
            (item.horizon, item.minimum_months, item.maximum_months, item.maximum_inclusive) for item in self.horizons
        )
        if actual != expected:
            raise ValueError("policy must use the approved medium- and long-term horizon ranges")
        return self


class WebResearchPolicyV1(StrictContractModel):
    """Research independence, validation, and untrusted-content policy."""

    schema_id: Literal["detailed-analysis.web-research-policy"]
    schema_version: Literal[1]
    artifact_id: Identifier
    policy_version: int = Field(ge=1)
    effective_at: Timestamp
    permitted_roles: tuple[AgentRoleValue, ...] = Field(strict=False, min_length=5, max_length=5)
    fixed_search_iteration_limit: Literal[None]
    search_results_are_evidence: Literal[False]
    original_document_validation_required: Literal[True]
    external_content_is_trusted_instruction: Literal[False]
    share_peer_search_content_during_initial_analysis: Literal[False]
    distribute_verified_common_evidence_to: tuple[Literal["codex_worker", "claude_worker"], ...] = Field(
        strict=False, min_length=2, max_length=2
    )
    completion_criteria: tuple[NonEmptyString, ...] = Field(strict=False, min_length=1)

    @field_validator("effective_at")
    @classmethod
    def ensure_valid_effective_at(cls, value: str) -> str:
        """Validate the policy effective timestamp."""
        _parse_rfc3339(value, "effective_at")
        return value

    @model_validator(mode="after")
    def validate_roles_and_distribution(self) -> WebResearchPolicyV1:
        """Require every approved role and equal worker evidence distribution."""
        expected_roles = (
            "orchestrator",
            "codex_worker",
            "claude_worker",
            "primary_reviewer",
            "antigravity_auditor",
        )
        if tuple(self.permitted_roles) != expected_roles:
            raise ValueError("web research policy must list every approved role in stable order")
        if self.distribute_verified_common_evidence_to != ("codex_worker", "claude_worker"):
            raise ValueError("verified common evidence must be distributed to both workers")
        return self


class ReviewAuditPolicyV1(StrictContractModel):
    """Approved review, materiality, and conditional audit boundaries."""

    schema_id: Literal["detailed-analysis.review-audit-policy"]
    schema_version: Literal[1]
    artifact_id: Identifier
    policy_version: int = Field(ge=1)
    effective_at: Timestamp
    finding_classifications: tuple[FindingClassificationValue, ...] = Field(strict=False, min_length=8, max_length=8)
    material_impact_types: tuple[MaterialityImpactTypeValue, ...] = Field(strict=False, min_length=5, max_length=5)
    audit_gate_conditions: tuple[
        Literal[
            "primary_review_response_and_research_complete",
            "two_substantiated_positions_unresolved",
            "not_mechanically_resolvable",
            "material",
            "neutral_packet_valid",
            "not_already_validly_audited",
        ],
        ...,
    ] = Field(strict=False, min_length=6, max_length=6)
    valid_logical_audits_per_dispute: Literal[1]
    disputes_per_audit_session: Literal[1]
    antigravity_is_conditional_auditor_only: Literal[True]
    audit_workspace_read_only: Literal[True]
    automatic_retry_allowed: Literal[False]
    classification_pending_blocks_review_audit_and_completion: Literal[True]

    @field_validator("effective_at")
    @classmethod
    def ensure_valid_effective_at(cls, value: str) -> str:
        """Validate the policy effective timestamp."""
        _parse_rfc3339(value, "effective_at")
        return value

    @model_validator(mode="after")
    def validate_approved_values(self) -> ReviewAuditPolicyV1:
        """Keep all fixed ADR-0004 values complete and in stable order."""
        if self.finding_classifications != tuple(FindingClassification):
            raise ValueError("review audit policy must contain the eight approved finding classifications")
        expected_material_types = tuple(
            item for item in MaterialityImpactType if item is not MaterialityImpactType.OTHER
        )
        if self.material_impact_types != expected_material_types:
            raise ValueError("review audit policy must contain the five approved material impact types")
        expected_gate_conditions = (
            "primary_review_response_and_research_complete",
            "two_substantiated_positions_unresolved",
            "not_mechanically_resolvable",
            "material",
            "neutral_packet_valid",
            "not_already_validly_audited",
        )
        if self.audit_gate_conditions != expected_gate_conditions:
            raise ValueError("review audit policy must contain the six approved audit gate conditions")
        return self


class SessionContinuationPolicyV1(StrictContractModel):
    """Approved logical-session continuation and isolation policy."""

    schema_id: Literal["detailed-analysis.session-continuation-policy"]
    schema_version: Literal[1]
    artifact_id: Identifier
    policy_version: int = Field(ge=1)
    effective_at: Timestamp
    session_modes: tuple[Literal["new", "resume", "native_fork", "reconstructed_fork"], ...] = Field(
        strict=False, min_length=4, max_length=4
    )
    resume_requires_same: tuple[
        Literal["task", "role", "logical_agent", "purpose", "logical_session_settings"], ...
    ] = Field(strict=False, min_length=5, max_length=5)
    new_session_required_when_changed: tuple[
        Literal[
            "task", "security_or_market", "purpose", "role", "logical_agent", "model_family", "safety", "permissions"
        ],
        ...,
    ] = Field(strict=False, min_length=8, max_length=8)
    max_active_runs_per_logical_session: Literal[1]
    persistent_session_queue_enabled: Literal[False]
    cross_role_provider_session_resume_allowed: Literal[False]
    resume_failure_automatic_fallback_allowed: Literal[False]
    settings_immutable_within_logical_session: Literal[True]
    duplicate_operation_rejected_as: Literal["session_busy"]

    @field_validator("effective_at")
    @classmethod
    def ensure_valid_effective_at(cls, value: str) -> str:
        """Validate the policy effective timestamp."""
        _parse_rfc3339(value, "effective_at")
        return value

    @model_validator(mode="after")
    def validate_approved_modes(self) -> SessionContinuationPolicyV1:
        """Keep the approved modes and identity boundaries complete and ordered."""
        if self.session_modes != ("new", "resume", "native_fork", "reconstructed_fork"):
            raise ValueError("session continuation policy must contain all approved session modes")
        if self.resume_requires_same != ("task", "role", "logical_agent", "purpose", "logical_session_settings"):
            raise ValueError("session continuation policy must preserve all resume identity fields")
        expected_new_conditions = (
            "task",
            "security_or_market",
            "purpose",
            "role",
            "logical_agent",
            "model_family",
            "safety",
            "permissions",
        )
        if self.new_session_required_when_changed != expected_new_conditions:
            raise ValueError("session continuation policy must list every new-session boundary")
        return self


class MarketProfileV1(StrictContractModel):
    """Versioned XTKS interpretation profile without embedded holiday guesses."""

    schema_id: Literal["detailed-analysis.market-profile"]
    schema_version: Literal[1]
    artifact_id: Identifier
    profile_version: int = Field(ge=1)
    mic: Literal["XTKS"]
    timezone: Literal["Asia/Tokyo"]
    instrument_scope: Literal["domestic_cash_equity"]
    regular_session_close_local: Literal["15:30:00"]
    calendar_authority: Literal["JPX"]
    calendar_reference: NonEmptyString
    calendar_reviewed_on: str
    calendar_data_status: KnowledgeStatusValue
    enabled_for_runtime_date_resolution: bool

    @field_validator("calendar_reviewed_on")
    @classmethod
    def ensure_valid_review_date(cls, value: str) -> str:
        """Validate the date on which the market rule was reviewed."""
        _validate_iso_date(value, "calendar_reviewed_on")
        return value

    @model_validator(mode="after")
    def ensure_calendar_is_verified_before_enablement(self) -> MarketProfileV1:
        """Keep runtime date resolution disabled while calendar data is unknown."""
        if self.enabled_for_runtime_date_resolution and self.calendar_data_status is not KnowledgeStatus.KNOWN:
            raise ValueError("runtime date resolution requires known calendar data")
        return self


class OfficialReferenceV1(StrictContractModel):
    """Official document reviewed for a source condition."""

    reference_id: Identifier
    document_type: Identifier
    url: NonEmptyString
    document_version_or_sha256: NonEmptyString
    reviewed_on: str

    @field_validator("reviewed_on")
    @classmethod
    def ensure_valid_reviewed_on(cls, value: str) -> str:
        """Validate external-condition review date."""
        _validate_iso_date(value, "reviewed_on")
        return value


class SourceConditionV1(StrictContractModel):
    """One required source condition with explicit knowledge status."""

    category: SourceConditionCategoryValue
    knowledge_status: KnowledgeStatusValue
    summary: NonEmptyString
    official_reference_ids: tuple[Identifier, ...] = Field(strict=False)


class SourceApprovalV1(StrictContractModel):
    """Machine-readable approval record governing source use and transfer."""

    schema_id: Literal["detailed-analysis.source-approval"]
    schema_version: Literal[1]
    artifact_id: Identifier
    approval_id: Identifier
    approval_version: int = Field(ge=1)
    source_id: Identifier
    status: ApprovalStatusValue
    provider: NonEmptyString
    service: NonEmptyString
    dataset: NonEmptyString
    reviewer: NonEmptyString
    reviewed_on: str
    approved_by: NonEmptyString | None
    effective_on: str | None
    recheck_due_on: str | None
    status_reason: NonEmptyString
    usage_entity: NonEmptyString
    usage_purposes: tuple[NonEmptyString, ...] = Field(strict=False, min_length=1)
    contract_plan: NonEmptyString
    credential_scope_alias: Identifier | None
    secret_value_storage_allowed: Literal[False]
    online_use_allowed: bool
    external_agent_transfer_allowed: bool
    official_references: tuple[OfficialReferenceV1, ...] = Field(strict=False, min_length=1)
    conditions: tuple[SourceConditionV1, ...] = Field(strict=False, min_length=1)

    @field_validator("reviewed_on", "effective_on", "recheck_due_on")
    @classmethod
    def ensure_valid_dates(cls, value: str | None, info: object) -> str | None:
        """Validate dates without deciding whether a live approval is still current."""
        if value is not None:
            field_name = getattr(info, "field_name", "date")
            _validate_iso_date(value, field_name)
        return value

    @model_validator(mode="after")
    def validate_approval(self) -> SourceApprovalV1:
        """Require complete external-condition evidence before enabling a source."""
        reference_ids = [reference.reference_id for reference in self.official_references]
        if len(reference_ids) != len(set(reference_ids)):
            raise ValueError("official reference IDs must be unique")
        categories = [condition.category for condition in self.conditions]
        if len(categories) != len(set(categories)):
            raise ValueError("source condition categories must be unique")
        if set(categories) != set(SourceConditionCategory):
            raise ValueError("source approval must cover every required condition category")
        for condition in self.conditions:
            if set(condition.official_reference_ids) - set(reference_ids):
                raise ValueError("source conditions must reference declared official documents")

        incomplete = any(
            condition.knowledge_status in {KnowledgeStatus.UNKNOWN, KnowledgeStatus.NOT_RETRIEVED}
            for condition in self.conditions
        )
        if self.status is ApprovalStatus.APPROVED:
            if self.approved_by is None or self.effective_on is None or self.recheck_due_on is None:
                raise ValueError("approved sources require approver, effective date, and recheck date")
            if incomplete:
                raise ValueError("approved sources must not contain unknown or not-retrieved conditions")
        if self.online_use_allowed and self.status is not ApprovalStatus.APPROVED:
            raise ValueError("online use requires approved source status")
        if self.external_agent_transfer_allowed and not self.online_use_allowed:
            raise ValueError("external Agent transfer requires online use approval")
        return self


class QuantitativeSettingV1(StrictContractModel):
    """Numeric provider setting with explicit unknown and unsupported states."""

    knowledge_status: KnowledgeStatusValue
    value: int | float | None
    unit: Identifier
    evidence_reference_ids: tuple[Identifier, ...] = Field(strict=False)

    @model_validator(mode="after")
    def validate_setting(self) -> QuantitativeSettingV1:
        """Only known settings may carry a non-negative numeric value."""
        if self.knowledge_status is KnowledgeStatus.KNOWN:
            if self.value is None:
                raise ValueError("known settings require a value")
            if self.value < 0:
                raise ValueError("quantitative settings must not be negative")
        elif self.value is not None:
            raise ValueError("unknown, unsupported, or not-retrieved settings require null")
        return self


class CachePolicyV1(StrictContractModel):
    """Cache constraints included in fingerprint compatibility checks."""

    enabled: bool
    ttl_seconds: QuantitativeSettingV1
    varies_by_credential_scope: Literal[True]
    varies_by_freshness_and_period: Literal[True]
    varies_by_source_policy_version: Literal[True]


class BatchPolicyV1(StrictContractModel):
    """Provider batch capability and verified maximum size."""

    support_status: KnowledgeStatusValue
    max_batch_size: QuantitativeSettingV1


class CooldownPolicyV1(StrictContractModel):
    """Provider cooldown behavior without automatic retries."""

    honor_provider_retry_after: Literal[True]
    default_cooldown_seconds: QuantitativeSettingV1
    automatic_retry_allowed: Literal[False]


class SourceProfileV1(StrictContractModel):
    """Versioned coordinator controls for one approved source."""

    schema_id: Literal["detailed-analysis.source-profile"]
    schema_version: Literal[1]
    artifact_id: Identifier
    source_id: Identifier
    profile_version: int = Field(ge=1)
    policy_version: int = Field(ge=1)
    source_approval_reference: ArtifactReferenceV1
    source_approval_status: ApprovalStatusValue
    enabled: bool
    rate_domain: Identifier
    credential_scope_alias: Identifier | None
    egress_scope: Identifier
    max_concurrency: int = Field(ge=1)
    min_interval_seconds: QuantitativeSettingV1
    rate_requests: QuantitativeSettingV1
    rate_window_seconds: QuantitativeSettingV1
    burst: int = Field(ge=1)
    cache_policy: CachePolicyV1
    batch_policy: BatchPolicyV1
    cooldown_policy: CooldownPolicyV1
    direct_connection_fallback_allowed: Literal[False]

    @model_validator(mode="after")
    def validate_profile_enablement(self) -> SourceProfileV1:
        """Apply conservative controls until all required current values are known."""
        if self.source_approval_reference.schema_id != "detailed-analysis.source-approval":
            raise ValueError("source profile must reference a source-approval artifact")
        if self.enabled:
            if self.source_approval_status is not ApprovalStatus.APPROVED:
                raise ValueError("enabled source profiles require approved source status")
            if (
                self.min_interval_seconds.knowledge_status is not KnowledgeStatus.KNOWN
                or self.min_interval_seconds.value is None
                or self.min_interval_seconds.value <= 0
            ):
                raise ValueError("enabled source profiles require a known positive min_interval")
        elif self.min_interval_seconds.knowledge_status is not KnowledgeStatus.KNOWN:
            if self.max_concurrency != 1 or self.burst != 1:
                raise ValueError("unverified profiles must use max_concurrency 1 and burst 1")
        return self
