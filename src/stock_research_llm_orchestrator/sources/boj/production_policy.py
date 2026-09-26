"""Exact owner-approved BOJ v2 binding and production gates."""

import hashlib
from datetime import date
from pathlib import Path

from stock_research_llm_orchestrator.configuration import validate_configuration
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.external_requests import GateScope
from stock_research_llm_orchestrator.requests.models import SourceBinding, bind_source
from stock_research_llm_orchestrator.requests.production import GateKeys, GateLimit, HierarchicalGatePolicy
from stock_research_llm_orchestrator.sources.boj.code_api import BojFxCodeAdapter
from stock_research_llm_orchestrator.sources.protocol import CredentialFreeSourceIntent, SourceParameter


APPROVAL_SHA256 = "1d1e294d48876acbb2c69f12ab85617b90d8c0086f9c90f2f67d5b423a12d42a"
PROFILE_SHA256 = "563bdfb349b0deb6698c8000293d8fd45f294e4ee9fe561f2476ea5c61d59596"


def load_boj_binding(config: Path, evaluation_date: date) -> SourceBinding:
    """Read exact configuration bytes again immediately before admission/send."""
    for relative in ("source-approvals/boj/v2.yaml", "source-profiles/boj/v2.yaml"):
        path = config / relative
        if any(part.is_symlink() for part in (path, *path.parents)):
            raise ValueError("boj_configuration_symlink")
    binding = bind_source(
        validate_configuration(config),
        source_id="boj",
        profile_version=2,
        policy_version=2,
        evaluation_date=evaluation_date,
    )
    if not isinstance(binding, SourceBinding):
        raise ValueError("invalid_boj_binding")
    a, p = binding.approval, binding.profile
    if not (
        binding.approval_reference.sha256 == APPROVAL_SHA256
        and binding.profile_reference.sha256 == PROFILE_SHA256
        and a.artifact_id == "boj-source-approval-v2"
        and a.approval_version == 2
        and p.artifact_id == "boj-source-profile-v2"
        and p.profile_version == p.policy_version == 2
        and a.source_id == p.source_id == "boj"
        and p.rate_domain == "boj-stat-search-api"
        and p.egress_scope == "default-egress"
        and a.credential_scope_alias is p.credential_scope_alias is None
        and not a.external_agent_transfer_allowed
        and binding.limits()[:5] == (1, 60, 1, 60, 1)
    ):
        raise ValueError("unsupported_boj_binding")
    return binding


def fx_intent(start: date, end: date) -> CredentialFreeSourceIntent:
    """Build the canonical month request covering a fixed daily period."""
    if start > end:
        raise ValueError("invalid_fx_period")
    return BojFxCodeAdapter().build_intent(
        "fx-daily-code",
        tuple(
            SourceParameter(name=name, value=value)
            for name, value in (
                ("code", "FXERD04"),
                ("db", "FM08"),
                ("end_date", end.strftime("%Y%m")),
                ("format", "json"),
                ("lang", "en"),
                ("start_date", start.strftime("%Y%m")),
            )
        ),
    )


def fingerprint(intent: CredentialFreeSourceIntent) -> str:
    """Bind complete request bytes, approval/profile and anonymous egress scope."""
    canonical = BojFxCodeAdapter().build_intent(intent.operation, intent.parameters)
    if intent != canonical:
        raise ValueError("invalid_boj_intent")
    return hashlib.sha256(
        (
            intent.model_dump_json()
            + APPROVAL_SHA256
            + PROFILE_SHA256
            + "anonymous:default-egress:source-acquisition:v1"
        ).encode()
    ).hexdigest()


def gate_policy() -> HierarchicalGatePolicy:
    """Apply the conservative owner limit to every shared gate."""
    return HierarchicalGatePolicy(
        limits={
            scope: GateLimit(max_concurrency=1, min_interval_seconds=60, requests_per_window=1, window_seconds=60)
            for scope in GateScope
        }
    )


def gate_keys(task_id: str) -> GateKeys:
    """Keep provider/egress limits shared across callers and processes."""
    return GateKeys(
        egress="default-egress",
        provider="boj-stat-search-api",
        origin="www.stat-search.boj.or.jp",
        credential="anonymous",
        operation="fx-daily-code",
        task=task_id,
        role="source-acquisition",
    )
