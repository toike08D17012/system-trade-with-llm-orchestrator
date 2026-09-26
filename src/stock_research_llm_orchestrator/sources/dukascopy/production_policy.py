"""Exact owner-approved Dukascopy v1 binding and production gates."""

import hashlib
from datetime import date
from pathlib import Path

from stock_research_llm_orchestrator.configuration import validate_configuration
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.external_requests import GateScope
from stock_research_llm_orchestrator.requests.models import SourceBinding, bind_source
from stock_research_llm_orchestrator.requests.production import GateKeys, GateLimit, HierarchicalGatePolicy
from stock_research_llm_orchestrator.sources.protocol import CredentialFreeSourceIntent


APPROVAL_SHA256 = "f276cbcda92a35b3491d4e9196a1a1da06abba7465e8628c7d75f55cdb9534a5"
PROFILE_SHA256 = "ba92b804e94a32c33fbbbfb15db5ce4ab4e8cf315856ca34f6bdda011d6b526d"


def load_dukascopy_binding(config: Path, evaluation_date: date) -> SourceBinding:
    """Read exact configuration bytes again immediately before admission/send."""
    for relative in ("source-approvals/dukascopy/v1.yaml", "source-profiles/dukascopy/v1.yaml"):
        path = config / relative
        if any(part.is_symlink() for part in (path, *path.parents)):
            raise ValueError("dukascopy_configuration_symlink")
    binding = bind_source(
        validate_configuration(config),
        source_id="dukascopy",
        profile_version=1,
        policy_version=1,
        evaluation_date=evaluation_date,
    )
    if not isinstance(binding, SourceBinding):
        raise ValueError("invalid_dukascopy_binding")
    a, p = binding.approval, binding.profile
    if not (
        binding.approval_reference.sha256 == APPROVAL_SHA256
        and binding.profile_reference.sha256 == PROFILE_SHA256
        and a.artifact_id == "dukascopy-source-approval-v1"
        and a.approval_version == 1
        and p.artifact_id == "dukascopy-source-profile-v1"
        and p.profile_version == p.policy_version == 1
        and a.source_id == p.source_id == "dukascopy"
        and p.rate_domain == "dukascopy-daily-candles"
        and p.egress_scope == "default-egress"
        and a.credential_scope_alias is p.credential_scope_alias is None
        and not a.external_agent_transfer_allowed
        and binding.limits()[:5] == (1, 2, 30, 60, 1)
    ):
        raise ValueError("unsupported_dukascopy_binding")
    return binding


def fingerprint(intent: CredentialFreeSourceIntent) -> str:
    """Bind complete request bytes, approval/profile and anonymous egress scope."""
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
            scope: GateLimit(max_concurrency=1, min_interval_seconds=2, requests_per_window=30, window_seconds=60)
            for scope in GateScope
        }
    )


def gate_keys(task_id: str) -> GateKeys:
    """Keep provider/egress limits shared across callers and processes."""
    return GateKeys(
        egress="default-egress",
        provider="dukascopy-daily-candles",
        origin="jetta.dukascopy.com",
        credential="anonymous",
        operation="fx-daily-bid",
        task=task_id,
        role="source-acquisition",
    )
