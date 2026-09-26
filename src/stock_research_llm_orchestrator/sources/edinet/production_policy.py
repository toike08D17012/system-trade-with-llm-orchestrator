"""Exact owner-approved EDINET v2 binding and production gates."""

from datetime import date
from pathlib import Path

from stock_research_llm_orchestrator.configuration import validate_configuration
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.external_requests import GateScope
from stock_research_llm_orchestrator.requests.models import SourceBinding, bind_source
from stock_research_llm_orchestrator.requests.production import GateKeys, GateLimit, HierarchicalGatePolicy


APPROVAL_SHA256 = "91100f98512569f612348f0e0d2d8e300d256af6886e880148e0832374288d66"
PROFILE_SHA256 = "36679945bb6969a9214c2a88f02687c8880050cf893ca934bfb63b1defddf5c7"


def load_edinet_binding(config: Path, evaluation_date: date) -> SourceBinding:
    """Read exact configuration bytes again immediately before admission/send."""
    for relative in ("source-approvals/edinet/v2.yaml", "source-profiles/edinet/v2.yaml"):
        path = config / relative
        if any(part.is_symlink() for part in (path, *path.parents)):
            raise ValueError("edinet_configuration_symlink")
    binding = bind_source(
        validate_configuration(config),
        source_id="edinet",
        profile_version=2,
        policy_version=2,
        evaluation_date=evaluation_date,
    )
    if not isinstance(binding, SourceBinding):
        raise ValueError("invalid_edinet_binding")
    a, p = binding.approval, binding.profile
    if not (
        binding.approval_reference.sha256 == APPROVAL_SHA256
        and binding.profile_reference.sha256 == PROFILE_SHA256
        and a.artifact_id == "edinet-source-approval-v2"
        and a.approval_version == 2
        and p.artifact_id == "edinet-source-profile-v2"
        and p.profile_version == p.policy_version == 2
        and a.source_id == p.source_id == "edinet"
        and p.rate_domain == "edinet-api"
        and p.egress_scope == "default-egress"
        and a.credential_scope_alias == p.credential_scope_alias == "edinet-api-key"
        and not a.external_agent_transfer_allowed
        and binding.limits()[:5] == (1, 60, 1, 60, 1)
    ):
        raise ValueError("unsupported_edinet_binding")
    return binding


def gate_policy() -> HierarchicalGatePolicy:
    """Apply the conservative owner limit to every shared gate."""
    return HierarchicalGatePolicy(
        limits={
            scope: GateLimit(max_concurrency=1, min_interval_seconds=60, requests_per_window=1, window_seconds=60)
            for scope in GateScope
        }
    )


def gate_keys(task_id: str, operation: str) -> GateKeys:
    """Keep provider/egress limits shared across callers and processes."""
    return GateKeys(
        egress="default-egress",
        provider="edinet-api",
        origin="api.edinet-fsa.go.jp",
        credential="edinet-api-key",
        operation=operation,
        task=task_id,
        role="source-acquisition",
    )
