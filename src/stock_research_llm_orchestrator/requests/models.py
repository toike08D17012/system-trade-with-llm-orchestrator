"""Validated source bindings for production request admission."""

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

from stock_research_llm_orchestrator.configuration import ConfigurationArtifact
from stock_research_llm_orchestrator.contracts.base import ArtifactReferenceV1
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.policies import (
    QuantitativeSettingV1,
    SourceApprovalV1,
    SourceProfileV1,
)


_BINDING_TOKEN = object()


@dataclass(frozen=True)
class AttemptResult:
    """Sanitized rejection of a source binding."""

    status: Literal["succeeded", "failed", "blocked", "rejected"]
    reason_code: str


@dataclass(frozen=True)
class SourceBinding:
    """Factory-selected configuration snapshot; construct through bind_source only."""

    approval: SourceApprovalV1
    profile: SourceProfileV1
    approval_reference: ArtifactReferenceV1
    profile_reference: ArtifactReferenceV1
    evaluation_date: date
    _factory_token: object | None = field(default=None, repr=False, compare=False)

    def limits(self) -> tuple[int, float, int, float, int, str]:
        """Validate the supported source subset and return its gate identity."""
        a, p = self.approval, self.profile
        if not (
            self._factory_token is _BINDING_TOKEN
            and a.status == "approved"
            and a.online_use_allowed
            and p.enabled
            and a.effective_on is not None
            and a.recheck_due_on is not None
            and date.fromisoformat(a.effective_on) <= self.evaluation_date < date.fromisoformat(a.recheck_due_on)
            and p.source_id == a.source_id
            and p.source_approval_status == a.status
            and p.credential_scope_alias == a.credential_scope_alias
            and p.source_approval_reference == self.approval_reference
            and self.profile_reference.artifact_id == p.artifact_id
            and self.approval_reference.artifact_id == a.artifact_id
            and p.max_concurrency == 1
            and p.burst == 1
            and not p.cache_policy.enabled
            and p.batch_policy.support_status == "unsupported"
            and p.batch_policy.max_batch_size.knowledge_status == "unsupported"
            and p.cooldown_policy.default_cooldown_seconds.knowledge_status != "known"
            and not p.direct_connection_fallback_allowed
        ):
            raise ValueError("unsupported_source_binding")
        interval = _positive(p.min_interval_seconds, "second")
        window = _positive(p.rate_window_seconds, "second")
        count = _positive(p.rate_requests, "request")
        if type(p.rate_requests.value) is not int:
            raise ValueError("unsupported_request_limit")
        return (1, interval, int(count), window, 1, p.cooldown_policy.default_cooldown_seconds.knowledge_status)


def _positive(setting: QuantitativeSettingV1, unit: str) -> float:
    value = setting.value
    if (
        setting.knowledge_status != "known"
        or setting.unit != unit
        or value is None
        or isinstance(value, bool)
        or not math.isfinite(value)
        or value <= 0
    ):
        raise ValueError("unsupported_quantitative_setting")
    return float(value)


def bind_source(
    artifacts: tuple[ConfigurationArtifact, ...],
    source_id: str,
    profile_version: int,
    policy_version: int,
    evaluation_date: date,
) -> SourceBinding | AttemptResult:
    """Select exact validated artifacts, rejecting missing or ambiguous selections.

    Args:
        artifacts: Snapshots returned by validate_configuration, never arbitrary models.
        source_id: Approved source identifier.
        profile_version: Exact profile version.
        policy_version: Exact policy version.
        evaluation_date: Explicit offline policy evaluation date.

    Returns:
        A supported binding or a sanitized rejection with no invented events.
    """
    try:
        profiles = [
            item
            for item in artifacts
            if isinstance(item.model, SourceProfileV1)
            and item.model.source_id == source_id
            and item.model.profile_version == profile_version
            and item.model.policy_version == policy_version
        ]
        if len(profiles) != 1:
            raise ValueError("ambiguous_profile")
        profile_artifact = profiles[0]
        profile = SourceProfileV1.model_validate(profile_artifact.model.model_dump(warnings=False))
        approvals = [
            item
            for item in artifacts
            if isinstance(item.model, SourceApprovalV1)
            and item.model.artifact_id == profile.source_approval_reference.artifact_id
        ]
        if len(approvals) != 1:
            raise ValueError("ambiguous_approval")
        approval_artifact = approvals[0]
        approval = SourceApprovalV1.model_validate(approval_artifact.model.model_dump(warnings=False))
        binding = SourceBinding(
            approval,
            profile,
            ArtifactReferenceV1(
                artifact_type="source_approval",
                artifact_id=approval.artifact_id,
                schema_id=approval.schema_id,
                schema_version=approval.schema_version,
                sha256=approval_artifact.sha256,
            ),
            ArtifactReferenceV1(
                artifact_type="source_profile",
                artifact_id=profile.artifact_id,
                schema_id=profile.schema_id,
                schema_version=profile.schema_version,
                sha256=profile_artifact.sha256,
            ),
            evaluation_date,
            _BINDING_TOKEN,
        )
        binding.limits()
        return binding
    except ValueError, TypeError, AttributeError:
        return AttemptResult("rejected", "invalid_source_binding")
