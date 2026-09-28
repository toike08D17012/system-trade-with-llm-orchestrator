"""Pinned initial acquisition policy for nine private disclosure candidates."""

from datetime import date
from hashlib import sha256
from pathlib import Path
from typing import Literal

from stock_research_llm_orchestrator.configuration import validate_configuration
from stock_research_llm_orchestrator.contracts.base import StrictContractModel
from stock_research_llm_orchestrator.preparation.market_revalidation import _safe_path
from stock_research_llm_orchestrator.requests.models import SourceBinding, bind_source
from stock_research_llm_orchestrator.sources.protocol import CredentialFreeSourceIntent, SourceParameter


APPROVAL_SHA256 = "95b3ea402d2d113b18eb99464f6afc8179a1b403780a899efac6bb334e464923"
PROFILE_SHA256 = "71bac5b60ce42c562baa8ce4443f43d1d969361dcb0684340d58aa7945ff6748"
POLICY_SHA256 = "b6cd06ffa7c96ab9f352bf9f06ec1f1504640bff767a41c13e40126227a4287f"
CAMPAIGN = "toyota-latest-disclosure-nine-pdf-20260928"
KIND: Literal["toyota-disclosure-runtime-candidates"] = "toyota-disclosure-runtime-candidates"
OPERATION = "disclosure-pdf"


class DisclosureDocument(StrictContractModel):
    """Approved source location, without a preexisting accepted content hash."""

    key: str
    published_on: date
    url: str


class DisclosurePolicy(StrictContractModel):
    """Exact candidate cohort; never claim complete disclosure coverage."""

    version: Literal[1]
    security_code: Literal["7203"]
    documents: tuple[DisclosureDocument, ...]
    limitations: tuple[str, ...]


def load_policy(body: bytes) -> DisclosurePolicy:
    """Reject any change to the fixed targets or their declared dates."""
    if sha256(body).hexdigest() != POLICY_SHA256:
        raise ValueError("disclosure_policy_unapproved")
    return DisclosurePolicy.model_validate_json(body)


def load_binding(config: Path, on: date) -> SourceBinding:
    """Check exact v2 approval/profile bytes before admission and each send."""
    for relative, digest in (
        ("source-approvals/toyota-ir/v2.yaml", APPROVAL_SHA256),
        ("source-profiles/toyota-ir/v2.yaml", PROFILE_SHA256),
    ):
        if sha256(_safe_path(config / relative).read_bytes()).hexdigest() != digest:
            raise ValueError("disclosure_config_changed")
    result = bind_source(
        validate_configuration(config), source_id="toyota-ir", profile_version=2, policy_version=2, evaluation_date=on
    )
    if not isinstance(result, SourceBinding):
        raise ValueError("disclosure_binding_invalid")
    return result


def intent_for(policy: DisclosurePolicy, key: str) -> CredentialFreeSourceIntent:
    """Only allow the exact approved URL, never follow links found in a PDF."""
    document = next((d for d in policy.documents if d.key == key), None)
    if document is None:
        raise ValueError("disclosure_document_not_allowed")
    return CredentialFreeSourceIntent(
        source_id="toyota-ir",
        operation=OPERATION,
        origin="global.toyota",
        resource_key=key,
        parameters=(SourceParameter(name="url", value=document.url),),
    )


def fingerprint(intent: CredentialFreeSourceIntent, task_id: str) -> str:
    """Bind task, exact URL and all three versioned policy hashes."""
    return sha256(
        (intent.model_dump_json() + task_id + APPROVAL_SHA256 + PROFILE_SHA256 + POLICY_SHA256).encode()
    ).hexdigest()
