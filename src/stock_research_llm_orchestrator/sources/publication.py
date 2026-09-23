"""Bridge bounded source parsing to atomic raw artifact publication."""

from dataclasses import dataclass
from datetime import datetime

from stock_research_llm_orchestrator.contracts.base import StrictContractModel
from stock_research_llm_orchestrator.requests.production import (
    CommittedRawReference,
    RawPublicationIntent,
    RuntimeLease,
)
from stock_research_llm_orchestrator.requests.raw_artifacts import RawArtifactPublisher
from stock_research_llm_orchestrator.requests.transport import TemporaryRawCandidate
from stock_research_llm_orchestrator.sources.protocol import BoundedSourceResponse, SourceAdapter


@dataclass(frozen=True)
class PublishedSourceResult[SourceResult: StrictContractModel]:
    """A parsed source-native value tied to its committed exact raw reference."""

    reference: CommittedRawReference
    value: SourceResult


def publish_source_candidate[SourceResult: StrictContractModel](
    publisher: RawArtifactPublisher,
    candidate: TemporaryRawCandidate,
    intent: RawPublicationIntent,
    lease: RuntimeLease,
    started_at: datetime,
    committed_at: datetime,
    adapter: SourceAdapter[SourceResult],
) -> PublishedSourceResult[SourceResult]:
    """Parse staged exact bytes before publication and return the committed binding."""
    if adapter.source_id != intent.source_id:
        raise ValueError("source_adapter_intent_mismatch")
    parsed: list[SourceResult] = []

    def validate(body: bytes) -> None:
        staged = TemporaryRawCandidate(
            physical_attempt_id=candidate.physical_attempt_id,
            body=body,
            sha256=candidate.sha256,
            media_type=candidate.media_type,
            encoding=candidate.encoding,
        )
        parsed.append(adapter.parse(BoundedSourceResponse.from_candidate(staged)))

    reference = publisher.publish(candidate, intent, lease, started_at, committed_at, validate)
    if not parsed:
        # Idempotent replay verifies the committed bundle without invoking its validator.
        validate(candidate.body)
    if len(parsed) != 1:
        raise RuntimeError("source_candidate_validation_count_invalid")
    return PublishedSourceResult(reference=reference, value=parsed[0])
