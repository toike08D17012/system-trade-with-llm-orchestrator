"""Reviewed Yahoo gate composition; auxiliary transport remains unapproved."""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime

from pydantic import TypeAdapter

from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.external_requests import GateScope
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.research import AgentRoleValue
from stock_research_llm_orchestrator.requests.models import IDENTIFIER_ADAPTER, SourceBinding
from stock_research_llm_orchestrator.requests.production import (
    GateKeys,
    GateLimit,
    HierarchicalGatePolicy,
    ProductionCachePolicy,
    ProductionLogicalRequest,
)
from stock_research_llm_orchestrator.requests.transport import TransportValidationPolicy
from stock_research_llm_orchestrator.sources.yfinance.coordinated_session import YahooRequestIdentity
from stock_research_llm_orchestrator.sources.yfinance.models import YfinanceDailyIntent


_POLICY_TOKEN = object()
_ROLE = TypeAdapter(AgentRoleValue)
# A new artifact revision requires review, even if its numeric limits happen to match.
_APPROVAL_SHA256 = "ec7e4ce2b5e2276826a9a76c59de69ec6ddb12fa4ab85ed06cf6d6523126a60d"
_PROFILE_SHA256 = "b9a6a2d12b40118135d983d0769dab0368aed99c77435739ab579e8e2f535ef5"
_IDENTITIES = frozenset(
    {
        ("GET", "fc.yahoo.com", "cookie", "cookie_basic"),
        ("GET", "query1.finance.yahoo.com", "crumb", "crumb_basic"),
        ("GET", "query2.finance.yahoo.com", "crumb", "crumb_csrf"),
        ("GET", "query2.finance.yahoo.com", "chart", "chart"),
        ("GET", "guce.yahoo.com", "consent", "consent_form"),
        ("POST", "consent.yahoo.com", "consent", "consent_collect"),
        ("GET", "guce.yahoo.com", "consent", "consent_copy"),
    }
)


class YahooPolicyError(ValueError):
    """Sanitized failure before any physical request is admitted."""


def _validate_binding(binding: SourceBinding, evaluation_date: date) -> None:
    try:
        limits = binding.limits()
        a, p = binding.approval, binding.profile
        if not (
            a.source_id == p.source_id == "yfinance"
            and a.artifact_id == "yfinance-source-approval-v2"
            and a.approval_version == 2
            and p.artifact_id == "yfinance-source-profile-v3"
            and binding.approval_reference.sha256 == _APPROVAL_SHA256
            and binding.profile_reference.sha256 == _PROFILE_SHA256
            and p.profile_version == p.policy_version == 3
            and p.rate_domain == "yahoo-finance"
            and p.egress_scope == "default-egress"
            and a.credential_scope_alias is p.credential_scope_alias is None
            and not p.cooldown_policy.automatic_retry_allowed
            and p.cooldown_policy.honor_provider_retry_after
            and limits[:5] == (1, 2.5, 2, 5.0, 1)
            and a.effective_on is not None
            and a.recheck_due_on is not None
            and date.fromisoformat(a.effective_on) <= evaluation_date < date.fromisoformat(a.recheck_due_on)
        ):
            raise ValueError
    except ValueError, TypeError, AttributeError:
        raise YahooPolicyError("unsupported_yahoo_production_binding") from None


@dataclass(frozen=True)
class YahooProductionPolicy:
    """Sealed binding snapshot; returned gate dictionaries never mutate this policy."""

    _binding: SourceBinding = field(repr=False)
    _token: object | None = field(default=None, repr=False, compare=False)

    def validate(self, evaluation_date: date) -> None:
        """Recheck the approval interval at use time, not only at construction."""
        if self._token is not _POLICY_TOKEN:
            raise YahooPolicyError("unsealed_yahoo_production_policy")
        _validate_binding(self._binding, evaluation_date)

    @property
    def rate_domain(self) -> str:
        """Use the reviewed provider bucket for both queue and gate ownership."""
        self.validate(self._binding.evaluation_date)
        return self._binding.profile.rate_domain

    def gate_policy(self) -> HierarchicalGatePolicy:
        """Compose DEC-04 shared limits and exact profile v3 provider limits."""
        self.validate(self._binding.evaluation_date)
        shared = GateLimit(max_concurrency=1, min_interval_seconds=0, requests_per_window=60, window_seconds=60)
        provider = GateLimit(max_concurrency=1, min_interval_seconds=2.5, requests_per_window=2, window_seconds=5)
        return HierarchicalGatePolicy(
            limits={
                scope: shared
                if scope in {GateScope.GLOBAL, GateScope.EGRESS, GateScope.TASK, GateScope.ROLE}
                else provider
                for scope in GateScope
            }
        )

    def logical_request(
        self, *, logical_request_id: str, task_id: str, request_fingerprint: str, created_at: datetime
    ) -> ProductionLogicalRequest:
        """Derive persisted lineage without accepting caller-selected profile versions."""
        self.validate(created_at.date())
        return ProductionLogicalRequest(
            logical_request_id=logical_request_id,
            task_id=task_id,
            source_id=self._binding.profile.source_id,
            operation="daily-history",
            request_fingerprint=request_fingerprint,
            source_approval_version=self._binding.approval.approval_version,
            source_profile_version=self._binding.profile.profile_version,
            credential_scope_alias=self._binding.profile.credential_scope_alias,
            egress_scope=self._binding.profile.egress_scope,
            created_at=created_at.isoformat(),
        )

    def intent_fingerprint(self, intent: YfinanceDailyIntent, *, task_id: str, role: AgentRoleValue) -> str:
        """Bind the complete daily intent and consumer identity using canonical JSON v1."""
        self.validate(self._binding.evaluation_date)
        try:
            snapshot = YfinanceDailyIntent.model_validate(intent.model_dump(warnings=False))
            task = IDENTIFIER_ADAPTER.validate_python(task_id)
            agent_role = _ROLE.validate_python(role)
        except ValueError, AttributeError:
            raise YahooPolicyError("invalid_yahoo_intent_identity") from None
        payload = {
            "schema": "yfinance-daily-binding-v1",
            "intent": snapshot.model_dump(),
            "task_id": task,
            "role": agent_role.value,
            "source_id": "yfinance",
            "operation": "daily-history",
            "approval_sha256": self._binding.approval_reference.sha256,
            "profile_sha256": self._binding.profile_reference.sha256,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def cache_policy(self) -> ProductionCachePolicy:
        """Keep response cache disabled regardless of source cache applicability."""
        self.validate(self._binding.evaluation_date)
        return ProductionCachePolicy(applicable=True)

    def gate_keys(self, identity: YahooRequestIdentity, *, task_id: str, role: AgentRoleValue) -> GateKeys:
        """Build non-secret aliases without accepting caller policy overrides."""
        self.validate(self._binding.evaluation_date)
        self._validate_identity(identity)
        try:
            task = IDENTIFIER_ADAPTER.validate_python(task_id)
            agent_role = _ROLE.validate_python(role)
            return GateKeys(
                egress=self._binding.profile.egress_scope,
                provider=self.rate_domain,
                origin=identity.origin,
                credential="anonymous:yahoo-finance",
                operation=identity.endpoint,
                task=task,
                role=agent_role.value,
            )
        except ValueError:
            raise YahooPolicyError("invalid_yahoo_runtime_identity") from None

    def transport_policy_for(self, identity: YahooRequestIdentity) -> TransportValidationPolicy:
        """Apply the approved chart and memory-only auxiliary response formats."""
        self.validate(self._binding.evaluation_date)
        self._validate_identity(identity)
        return TransportValidationPolicy(
            max_response_bytes=None,
            allowed_media_types=("application/json",)
            if identity.resource_class == "chart"
            else (("text/plain",) if identity.resource_class == "crumb" else ("text/html", "text/plain")),
            allowed_encodings=("utf-8",),
        )

    @staticmethod
    def _validate_identity(identity: YahooRequestIdentity) -> None:
        if (identity.method, identity.origin, identity.resource_class, identity.endpoint) not in _IDENTITIES:
            raise YahooPolicyError("unapproved_yahoo_request_identity")


def compose_yahoo_production_policy(binding: SourceBinding) -> YahooProductionPolicy:
    """Compose only the reviewed lineage; this does not enable live acquisition."""
    _validate_binding(binding, binding.evaluation_date)
    return YahooProductionPolicy(binding, _POLICY_TOKEN)
