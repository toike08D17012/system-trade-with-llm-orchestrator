"""Composite EDINET adapter requiring both safe ZIP and XBRL validation."""

from pydantic import model_validator

from stock_research_llm_orchestrator.contracts.base import StrictContractModel
from stock_research_llm_orchestrator.sources.edinet.document_retrieval import (
    EdinetDocumentArchive,
    EdinetDocumentRetrievalAdapter,
)
from stock_research_llm_orchestrator.sources.edinet.xbrl_facts import EdinetXbrlFactExtractor, EdinetXbrlFactSet
from stock_research_llm_orchestrator.sources.protocol import (
    BoundedSourceResponse,
    CredentialFreeSourceIntent,
    SourceParameter,
)


class EdinetXbrlDocument(StrictContractModel):
    """One exact EDINET archive and all validated source-native XBRL content."""

    archive: EdinetDocumentArchive
    facts: EdinetXbrlFactSet

    @model_validator(mode="after")
    def require_one_archive_identity(self) -> EdinetXbrlDocument:
        """Prevent fact candidates from being rebound to different raw bytes."""
        if self.archive.archive_sha256 != self.facts.archive_sha256:
            raise ValueError("edinet_xbrl_document_hash_mismatch")
        return self


class EdinetXbrlDocumentAdapter:
    """Build retrieval intents and fully validate EDINET XBRL ZIP responses."""

    def __init__(self) -> None:
        """Compose the already-bounded inventory and fact parsers."""
        self._retrieval = EdinetDocumentRetrievalAdapter()
        self._extractor = EdinetXbrlFactExtractor()

    @property
    def source_id(self) -> str:
        """Return the immutable EDINET source identifier."""
        return "edinet"

    def build_intent(self, operation: str, parameters: tuple[SourceParameter, ...]) -> CredentialFreeSourceIntent:
        """Delegate to the approved XBRL ZIP retrieval mapping."""
        return self._retrieval.build_intent(operation, parameters)

    def parse(self, response: BoundedSourceResponse) -> EdinetXbrlDocument:
        """Require both safe archive inventory and source-native fact extraction."""
        archive = self._retrieval.parse(response)
        facts = self._extractor.extract(response, archive)
        return EdinetXbrlDocument(archive=archive, facts=facts)
