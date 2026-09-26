"""Revalidate a retained failed list without inventing a successful acquisition."""

import json
from collections.abc import Mapping
from datetime import datetime
from hashlib import sha256
from zoneinfo import ZoneInfo

from stock_research_llm_orchestrator.contracts.base import Sha256Hex, StrictContractModel
from stock_research_llm_orchestrator.preparation.market_revalidation import _timestamp
from stock_research_llm_orchestrator.sources.edinet.document_list import (
    EdinetDocument,
    EdinetDocumentList,
    EdinetDocumentListAdapter,
)
from stock_research_llm_orchestrator.sources.protocol import BoundedSourceResponse, CredentialFreeSourceIntent


class RetainedListFailure(StrictContractModel):
    """Original failure metadata remains a failure after successful offline parsing."""

    reason: str
    key: str
    sha256: Sha256Hex
    received_at: str
    source_intent: CredentialFreeSourceIntent


def revalidate_list(files: Mapping[str, bytes]) -> tuple[RetainedListFailure, EdinetDocumentList]:
    """Verify retained bytes, fixed request scope and parse result without network."""
    if not {"body.bin", "failure.json"} <= files.keys() or set(files) - {
        "body.bin",
        "failure.json",
        "offline-revalidation.json",
    }:
        raise ValueError("edinet_retained_list_inventory_invalid")
    failure = RetainedListFailure.model_validate_json(files["failure.json"])
    adapter = EdinetDocumentListAdapter()
    intent = failure.source_intent
    if (
        failure.reason != "source_validation_failed"
        or failure.key != "list"
        or intent != adapter.build_intent(intent.operation, intent.parameters)
        or dict((p.name, p.value) for p in intent.parameters) != {"date": "2026-06-10", "type": "2"}
        or sha256(files["body.bin"]).hexdigest() != failure.sha256
    ):
        raise ValueError("edinet_retained_list_binding_invalid")
    received = _timestamp(failure.received_at)
    listing = adapter.parse(
        BoundedSourceResponse(
            physical_attempt_id="offline-list-revalidation",
            body=files["body.bin"],
            sha256=failure.sha256,
            media_type="application/json",
            encoding="utf-8",
        )
    )
    processed = datetime.strptime(listing.processed_at, "%Y-%m-%d %H:%M").replace(tzinfo=ZoneInfo("Asia/Tokyo"))
    if processed > received:
        raise ValueError("edinet_retained_list_processed_after_receipt")
    if listing.requested_date != "2026-06-10":
        raise ValueError("edinet_retained_list_date_invalid")
    return failure, listing


def select_target(body: bytes, listing: EdinetDocumentList) -> EdinetDocument:
    """Select only the unique authorized, available annual report."""
    raw = {d["docID"]: d for d in json.loads(body)["results"]}
    matches = [
        d
        for d in listing.documents
        if d.security_code == "72030"
        and d.edinet_code is not None
        and d.document_type.value == "120"
        and d.period_start == "2025-04-01"
        and d.period_end == "2026-03-31"
        and d.withdrawal_status == "0"
        and d.xbrl_available
        and raw[d.document_id].get("legalStatus") in {"1", "2"}
        and raw[d.document_id].get("disclosureStatus") == "0"
    ]
    if len(matches) != 1:
        raise ValueError("edinet_acceptance_target_not_unique")
    return matches[0]
