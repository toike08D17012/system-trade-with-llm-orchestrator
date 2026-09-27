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


PRIOR_BODY_SHA256 = "5ceafc9ee61ee61ef3de8cf6911d7af26d3f8fa1cea875cb3865f1c770a3eca5"
PRIOR_FAILURE_SHA256 = "113bb3a9c90f30ee02b2f7c355dfd571b00e348de151b6c20580b0f79e5b6540"
PRIOR_APPROVAL_SHA256 = "c6ee8fe3da245c9b7ee741ff03ba90a58999d251e96079f2d9252de6480ed153"


class RetainedListFailure(StrictContractModel):
    """Original failure metadata remains a failure after successful offline parsing."""

    reason: str
    key: str
    sha256: Sha256Hex
    received_at: str
    source_intent: CredentialFreeSourceIntent


def revalidate_list(
    files: Mapping[str, bytes], *, prior: bool = False
) -> tuple[RetainedListFailure, EdinetDocumentList]:
    """Verify retained bytes, fixed request scope and parse result without network."""
    requested = "2024-06-25" if prior else "2026-06-10"
    if prior and (
        sha256(files["body.bin"]).hexdigest() != PRIOR_BODY_SHA256
        or sha256(files["failure.json"]).hexdigest() != PRIOR_FAILURE_SHA256
        or sha256(files["original-approval.yaml"]).hexdigest() != PRIOR_APPROVAL_SHA256
    ):
        raise ValueError("edinet_prior_retained_hash_mismatch")
    extras = {"offline-prior-revalidation.json", "original-approval.yaml"} if prior else set()
    if not {"body.bin", "failure.json"} <= files.keys() or set(files) - {
        "body.bin",
        "failure.json",
        "offline-revalidation.json",
        *extras,
    }:
        raise ValueError("edinet_retained_list_inventory_invalid")
    failure = RetainedListFailure.model_validate_json(files["failure.json"])
    adapter = EdinetDocumentListAdapter()
    intent = failure.source_intent
    if (
        failure.reason != "source_validation_failed"
        or failure.key != "list"
        or intent != adapter.build_intent(intent.operation, intent.parameters)
        or dict((p.name, p.value) for p in intent.parameters) != {"date": requested, "type": "2"}
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
    if listing.requested_date != requested:
        raise ValueError("edinet_retained_list_date_invalid")
    if prior:
        target = select_target(files["body.bin"], listing, "2023-04-01", "2024-03-31", "E02144")
        if target.document_id != "S100TR7I":
            raise ValueError("edinet_prior_document_mismatch")
    return failure, listing


def select_target(
    body: bytes,
    listing: EdinetDocumentList,
    start: str = "2025-04-01",
    end: str = "2026-03-31",
    expected_code: str | None = None,
) -> EdinetDocument:
    """Select only the unique authorized, available annual report."""
    raw = {d["docID"]: d for d in json.loads(body)["results"]}
    matches = [
        d
        for d in listing.documents
        if d.security_code == "72030"
        and d.edinet_code is not None
        and (expected_code is None or d.edinet_code == expected_code)
        and d.document_type.value == "120"
        and d.period_start == start
        and d.period_end == end
        and d.withdrawal_status == "0"
        and d.xbrl_available
        and raw[d.document_id].get("legalStatus") in {"1", "2"}
        and raw[d.document_id].get("disclosureStatus") == "0"
    ]
    if expected_code is not None and any(
        d.edinet_code == expected_code
        and d.period_start == start
        and d.period_end == end
        and d.document_type.value == "130"
        for d in listing.documents
    ):
        raise ValueError("edinet_amendment_selection_unresolved")
    if len(matches) != 1:
        raise ValueError("edinet_acceptance_target_not_unique")
    return matches[0]
