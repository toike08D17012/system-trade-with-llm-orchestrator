"""Deterministic handoff from a committed EDINET list to retrieval intents."""

from typing import Literal

from pydantic import model_validator

from stock_research_llm_orchestrator.contracts.base import Identifier, StrictContractModel
from stock_research_llm_orchestrator.requests.production import CommittedRawReference
from stock_research_llm_orchestrator.sources.edinet.document_list import EdinetDocumentList, EdinetDocumentType
from stock_research_llm_orchestrator.sources.edinet.xbrl_document import EdinetXbrlDocumentAdapter
from stock_research_llm_orchestrator.sources.protocol import CredentialFreeSourceIntent, SourceParameter
from stock_research_llm_orchestrator.sources.publication import PublishedSourceResult


_RetrievalOutcome = Literal["selected", "skipped"]
_RetrievalReason = Literal["selected", "withdrawn", "xbrl_unavailable"]


class EdinetRetrievalDecision(StrictContractModel):
    """One explicit retrieval or skip decision preserving filing identity."""

    sequence_number: int
    document_id: Identifier
    document_type: EdinetDocumentType
    parent_document_id: Identifier | None
    outcome: _RetrievalOutcome
    reason: _RetrievalReason
    retrieval_intent: CredentialFreeSourceIntent | None

    @model_validator(mode="after")
    def require_intent_only_for_selected_document(self) -> EdinetRetrievalDecision:
        """Keep every skip explicit and every selection bound to its document ID."""
        if self.outcome == "selected":
            if (
                self.reason != "selected"
                or self.retrieval_intent is None
                or self.retrieval_intent.resource_key != self.document_id
            ):
                raise ValueError("invalid_edinet_retrieval_selection")
        elif self.reason == "selected" or self.retrieval_intent is not None:
            raise ValueError("invalid_edinet_retrieval_skip")
        return self


class EdinetRetrievalPlan(StrictContractModel):
    """Retrieval decisions tied to the exact committed document-list raw."""

    source_raw_reference: CommittedRawReference
    requested_date: str
    decisions: tuple[EdinetRetrievalDecision, ...]

    @model_validator(mode="after")
    def require_unique_provider_order(self) -> EdinetRetrievalPlan:
        """Preserve an unambiguous provider sequence without reordering corrections."""
        sequence_numbers = tuple(decision.sequence_number for decision in self.decisions)
        document_ids = tuple(decision.document_id for decision in self.decisions)
        if sequence_numbers != tuple(sorted(sequence_numbers)) or len(sequence_numbers) != len(set(sequence_numbers)):
            raise ValueError("invalid_edinet_retrieval_sequence")
        if len(document_ids) != len(set(document_ids)):
            raise ValueError("duplicate_edinet_retrieval_document")
        return self


def plan_edinet_retrievals(
    published_list: PublishedSourceResult[EdinetDocumentList],
) -> EdinetRetrievalPlan:
    """Create explicit retrieval decisions from a validated and committed list."""
    if (
        published_list.reference.raw_schema_id != "edinet-document-list-raw"
        or published_list.reference.raw_schema_version != 1
    ):
        raise ValueError("invalid_edinet_list_raw_reference")
    adapter = EdinetXbrlDocumentAdapter()
    decisions: list[EdinetRetrievalDecision] = []
    for document in sorted(published_list.value.documents, key=lambda item: item.sequence_number):
        outcome: _RetrievalOutcome
        reason: _RetrievalReason
        intent: CredentialFreeSourceIntent | None
        if document.withdrawal_status == "1":
            outcome, reason, intent = "skipped", "withdrawn", None
        elif not document.xbrl_available:
            outcome, reason, intent = "skipped", "xbrl_unavailable", None
        else:
            outcome, reason = "selected", "selected"
            intent = adapter.build_intent(
                "document-retrieval",
                (
                    SourceParameter(name="document_id", value=document.document_id),
                    SourceParameter(name="type", value="1"),
                ),
            )
        decisions.append(
            EdinetRetrievalDecision(
                sequence_number=document.sequence_number,
                document_id=document.document_id,
                document_type=document.document_type,
                parent_document_id=document.parent_document_id,
                outcome=outcome,
                reason=reason,
                retrieval_intent=intent,
            )
        )
    return EdinetRetrievalPlan(
        source_raw_reference=published_list.reference,
        requested_date=published_list.value.requested_date,
        decisions=tuple(decisions),
    )
