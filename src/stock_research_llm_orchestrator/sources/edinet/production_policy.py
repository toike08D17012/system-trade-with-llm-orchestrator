"""Exact owner-approved EDINET v2 binding and production gates."""

from datetime import date
from pathlib import Path

from stock_research_llm_orchestrator.configuration import validate_configuration
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.external_requests import GateScope
from stock_research_llm_orchestrator.requests.models import SourceBinding, bind_source
from stock_research_llm_orchestrator.requests.production import GateKeys, GateLimit, HierarchicalGatePolicy


APPROVAL_SHA256 = "91100f98512569f612348f0e0d2d8e300d256af6886e880148e0832374288d66"
PROFILE_SHA256 = "36679945bb6969a9214c2a88f02687c8880050cf893ca934bfb63b1defddf5c7"


def load_edinet_binding(config: Path, evaluation_date: date, version: int = 2) -> SourceBinding:
    """Read exact configuration bytes again immediately before admission/send."""
    if version not in {2, 3, 4, 5}:
        raise ValueError("unsupported_edinet_version")
    approval_hash, profile_hash = (
        (APPROVAL_SHA256, PROFILE_SHA256)
        if version == 2
        else (
            "c6ee8fe3da245c9b7ee741ff03ba90a58999d251e96079f2d9252de6480ed153",
            "6ee813d1f40b638b34aa18c026711ac1ec81164639a62606a5e6cc57bae7b28a",
        )
    )
    if version == 4:
        approval_hash = "7b2e34dfd68a2514af8d9637e3ff87b4f229cd9ec465df855ca5114e09bb41ad"
        profile_hash = "f43f1963550365275338a8ff8080f56537fcc7ddb98f449f00196444a3209a52"
    if version == 5:
        approval_hash = "7fb134f39a723bd78c0962b48263a98c3655c1f34ff91cc3989d85bd3443ac5c"
        profile_hash = "65d60ef33e2527f6aae0418f6006681b88ddba630f30d22f24021a94fba5884e"
    for relative in (f"source-approvals/edinet/v{version}.yaml", f"source-profiles/edinet/v{version}.yaml"):
        path = config / relative
        if any(part.is_symlink() for part in (path, *path.parents)):
            raise ValueError("edinet_configuration_symlink")
    binding = bind_source(
        validate_configuration(config),
        source_id="edinet",
        profile_version=version,
        policy_version=version,
        evaluation_date=evaluation_date,
    )
    if not isinstance(binding, SourceBinding):
        raise ValueError("invalid_edinet_binding")
    a, p = binding.approval, binding.profile
    if not (
        binding.approval_reference.sha256 == approval_hash
        and binding.profile_reference.sha256 == profile_hash
        and a.artifact_id == f"edinet-source-approval-v{version}"
        and a.approval_version == version
        and p.artifact_id == f"edinet-source-profile-v{version}"
        and p.profile_version == p.policy_version == version
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
