"""Exact private-use binding for the eight previously accepted Toyota PDFs."""

from datetime import date
from hashlib import sha256
from pathlib import Path

from stock_research_llm_orchestrator.configuration import validate_configuration
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.external_requests import GateScope
from stock_research_llm_orchestrator.preparation.interim_ir import POLICY_SHA256, InterimPolicy
from stock_research_llm_orchestrator.preparation.market_revalidation import _safe_path
from stock_research_llm_orchestrator.requests.models import SourceBinding, bind_source
from stock_research_llm_orchestrator.requests.production import GateKeys, GateLimit, HierarchicalGatePolicy
from stock_research_llm_orchestrator.sources.protocol import CredentialFreeSourceIntent, SourceParameter


APPROVAL_SHA256 = "4b49d78279421dec274246ad2512a735d378a80245bbfe01ce1c7b653296ded4"
PROFILE_SHA256 = "14781858af29d6f9821cc5adfcf07fe1682498a2905a24dd8ed78719214c97af"


def load_binding(config: Path, on: date) -> SourceBinding:
    """Reject changes to the exact policy bytes before admission and physical send."""
    for relative, digest in (
        ("source-approvals/toyota-ir/v1.yaml", APPROVAL_SHA256),
        ("source-profiles/toyota-ir/v1.yaml", PROFILE_SHA256),
    ):
        if sha256(_safe_path(config / relative).read_bytes()).hexdigest() != digest:
            raise ValueError("toyota_ir_config_changed")
    result = bind_source(
        validate_configuration(config), source_id="toyota-ir", profile_version=1, policy_version=1, evaluation_date=on
    )
    if not isinstance(result, SourceBinding):
        raise ValueError("toyota_ir_binding_invalid")
    return result


def load_policy(body: bytes) -> InterimPolicy:
    """Never extend download targets from caller-controlled URLs."""
    if sha256(body).hexdigest() != POLICY_SHA256:
        raise ValueError("toyota_ir_policy_unapproved")
    return InterimPolicy.model_validate_json(body)


def intent_for(policy: InterimPolicy, key: str) -> CredentialFreeSourceIntent:
    """Bind the fixed PDF URL and hash to the parser/transport request."""
    document = next((d for d in policy.documents if d.key == key), None)
    if document is None:
        raise ValueError("toyota_ir_document_not_allowed")
    return CredentialFreeSourceIntent(
        source_id="toyota-ir",
        operation="interim-pdf",
        origin="global.toyota",
        resource_key=key,
        parameters=(
            SourceParameter(name="sha256", value=document.sha256),
            SourceParameter(name="url", value=document.url),
        ),
    )


def fingerprint(intent: CredentialFreeSourceIntent, task_id: str) -> str:
    """Persist URL, content, task and exact approval/profile policy identity."""
    return sha256(
        (intent.model_dump_json() + task_id + APPROVAL_SHA256 + PROFILE_SHA256 + POLICY_SHA256).encode()
    ).hexdigest()


def gates() -> HierarchicalGatePolicy:
    """Conservative internal start intervals, not provider-published rate limits."""
    return HierarchicalGatePolicy(
        limits={
            scope: GateLimit(max_concurrency=1, min_interval_seconds=60, requests_per_window=1, window_seconds=60)
            for scope in GateScope
        }
    )


def keys(task_id: str) -> GateKeys:
    """Share provider and origin limits across task IDs and processes."""
    return GateKeys(
        egress="default-egress",
        provider="toyota-public-ir",
        origin="global.toyota",
        credential="anonymous",
        operation="interim-pdf",
        task=task_id,
        role="source-acquisition",
    )
