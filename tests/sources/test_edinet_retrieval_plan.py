"""Tests for deterministic EDINET list-to-retrieval handoff."""

import pytest

from stock_research_llm_orchestrator.requests.production import CommittedRawReference
from stock_research_llm_orchestrator.sources import (
    EdinetDocument,
    EdinetDocumentList,
    EdinetDocumentType,
    PublishedSourceResult,
    plan_edinet_retrievals,
)


def _document(
    sequence: int,
    document_id: str,
    document_type: EdinetDocumentType,
    *,
    withdrawn: bool = False,
    xbrl: bool = True,
    parent: str | None = None,
) -> EdinetDocument:
    return EdinetDocument(
        sequence_number=sequence,
        document_id=document_id,
        edinet_code="E00001",
        security_code="72030",
        filer_name="Synthetic Motor Co., Ltd.",
        document_type=document_type,
        period_start="2025-04-01",
        period_end="2026-03-31",
        submitted_at="2026-09-22 09:00",
        description="Synthetic filing",
        parent_document_id=parent,
        withdrawal_status="1" if withdrawn else "0",
        xbrl_available=xbrl,
    )


def _published(documents: tuple[EdinetDocument, ...]) -> PublishedSourceResult[EdinetDocumentList]:
    reference = CommittedRawReference(
        publication_id="publication-list-1",
        task_id="task-1",
        logical_request_id="logical-list-1",
        physical_attempt_id="attempt-list-1",
        relative_path="acquisitions/logical-list-1/attempt-list-1",
        content_sha256="a" * 64,
        byte_count=100,
        raw_schema_id="edinet-document-list-raw",
        raw_schema_version=1,
        publication_generation=1,
    )
    value = EdinetDocumentList(
        requested_date="2026-09-22",
        processed_at="2026-09-22 18:00",
        provider_status="200",
        provider_message="OK",
        provider_result_count=len(documents),
        documents=documents,
    )
    return PublishedSourceResult(reference=reference, value=value)


def test_plan_selects_active_xbrl_and_keeps_skip_reasons() -> None:
    """Create retrieval intents without silently dropping unavailable filings."""
    plan = plan_edinet_retrievals(
        _published(
            (
                _document(3, "DOC003", EdinetDocumentType.QUARTERLY_REPORT, xbrl=False),
                _document(1, "DOC001", EdinetDocumentType.ANNUAL_REPORT),
                _document(2, "DOC002", EdinetDocumentType.SEMIANNUAL_REPORT, withdrawn=True),
            )
        )
    )

    assert tuple(decision.document_id for decision in plan.decisions) == ("DOC001", "DOC002", "DOC003")
    assert tuple(decision.reason for decision in plan.decisions) == (
        "selected",
        "withdrawn",
        "xbrl_unavailable",
    )
    selected = plan.decisions[0]
    assert selected.retrieval_intent is not None
    assert selected.retrieval_intent.resource_key == "DOC001"
    assert selected.retrieval_intent.parameters[1].value == "1"
    assert plan.source_raw_reference.content_sha256 == "a" * 64


def test_plan_preserves_amendment_as_a_separate_retrieval() -> None:
    """Retrieve an amendment independently while retaining its original document ID."""
    plan = plan_edinet_retrievals(
        _published(
            (
                _document(1, "ORIGINAL", EdinetDocumentType.ANNUAL_REPORT),
                _document(
                    2,
                    "AMENDMENT",
                    EdinetDocumentType.AMENDED_ANNUAL_REPORT,
                    parent="ORIGINAL",
                ),
            )
        )
    )

    assert tuple(decision.outcome for decision in plan.decisions) == ("selected", "selected")
    assert plan.decisions[1].parent_document_id == "ORIGINAL"
    assert plan.decisions[1].retrieval_intent is not None
    assert plan.decisions[1].retrieval_intent.resource_key == "AMENDMENT"


def test_plan_accepts_an_empty_approved_document_set() -> None:
    """Represent a day with no approved filings as an empty deterministic plan."""
    plan = plan_edinet_retrievals(_published(()))

    assert plan.decisions == ()


def test_plan_rejects_a_non_list_raw_reference() -> None:
    """Do not bind list-derived retrievals to a different raw schema."""
    published = _published((_document(1, "DOC001", EdinetDocumentType.ANNUAL_REPORT),))
    changed = PublishedSourceResult(
        reference=published.reference.model_copy(update={"raw_schema_id": "different-raw"}),
        value=published.value,
    )

    with pytest.raises(ValueError, match="^invalid_edinet_list_raw_reference$"):
        plan_edinet_retrievals(changed)
