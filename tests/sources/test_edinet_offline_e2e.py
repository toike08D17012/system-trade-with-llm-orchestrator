"""Offline EDINET E2E through production coordination and raw publication."""

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.external_requests import GateScope
from stock_research_llm_orchestrator.requests.production import (
    GateKeys,
    GateLimit,
    HierarchicalGatePolicy,
    ProductionCachePolicy,
    ProductionLogicalRequest,
    ProductionPhysicalAttempt,
    QueuePolicy,
    RawPublicationIntent,
    RuntimeLeasePolicy,
)
from stock_research_llm_orchestrator.requests.raw_artifacts import RawArtifactPublisher
from stock_research_llm_orchestrator.requests.storage import ProductionRequestRepository, initialize_runtime_storage
from stock_research_llm_orchestrator.requests.transport import (
    ProductionTransportCoordinator,
    TemporaryRawCandidate,
    TransportValidationPolicy,
    UntrustedTransportResponse,
)
from stock_research_llm_orchestrator.sources import (
    EdinetDocumentList,
    EdinetDocumentListAdapter,
    EdinetDocumentListParseError,
    PublishedSourceResult,
    SourceParameter,
    publish_source_candidate,
)


NOW = datetime(2026, 9, 23, tzinfo=UTC)
FIXTURE = Path(__file__).parents[1] / "fixtures/sources/edinet/document-list.json"
LIMIT = GateLimit(max_concurrency=1, min_interval_seconds=60, requests_per_window=1, window_seconds=60)
GATE_POLICY = HierarchicalGatePolicy(limits={scope: LIMIT for scope in GateScope})
TRANSPORT_POLICY = TransportValidationPolicy(
    max_response_bytes=32 * 1024 * 1024,
    allowed_media_types=("application/json",),
    allowed_encodings=("utf-8",),
)


def _run(
    tmp_path: Path, body: bytes
) -> tuple[ProductionRequestRepository, PublishedSourceResult[EdinetDocumentList], Path]:
    runtime = tmp_path / ".runtime"
    runtime.mkdir(mode=0o700)
    runs = tmp_path / "runs"
    runs.mkdir(mode=0o700)
    repository = initialize_runtime_storage(runtime)
    lease = repository.acquire_lease("owner-a", NOW, RuntimeLeasePolicy())
    adapter = EdinetDocumentListAdapter()
    source_intent = adapter.build_intent(
        "document-list",
        (SourceParameter(name="date", value="2026-09-22"), SourceParameter(name="type", value="2")),
    )
    logical = ProductionLogicalRequest(
        logical_request_id="logical-edinet-1",
        task_id="task-1",
        source_id="edinet",
        operation="document-list",
        request_fingerprint=hashlib.sha256(source_intent.model_dump_json().encode()).hexdigest(),
        source_approval_version=1,
        source_profile_version=1,
        credential_scope_alias="edinet-api-key",
        egress_scope="default-egress",
        created_at=NOW.isoformat(),
    )
    repository.admit_logical_request(
        logical,
        "edinet-api",
        ProductionCachePolicy(applicable=False),
        lease,
        NOW,
        QueuePolicy(),
    )
    assert repository.claim_next_queued("edinet-api", lease, NOW + timedelta(seconds=1)) is not None
    attempt = ProductionPhysicalAttempt(
        physical_attempt_id="attempt-edinet-1",
        logical_request_id=logical.logical_request_id,
        sequence_number=1,
        lease_generation=lease.generation,
        created_at=(NOW + timedelta(seconds=2)).isoformat(),
    )
    request = source_intent.to_transport_request(logical.logical_request_id, attempt.physical_attempt_id)
    response = UntrustedTransportResponse(
        status_code=200,
        body=body,
        media_type="application/json",
        encoding="utf-8",
        final_origin="api.edinet-fsa.go.jp",
        redirected=False,
    )
    coordinator = ProductionTransportCoordinator(repository, lambda _request: response, lambda: "permit-edinet-1")
    result = coordinator.execute(
        request,
        attempt,
        "reservation-edinet-1",
        GateKeys(
            egress="default-egress",
            provider="edinet-api",
            origin="api.edinet-fsa.go.jp",
            credential="edinet-api-key",
            operation="document-list",
            task="task-1",
            role="source-acquisition",
        ),
        GATE_POLICY,
        TRANSPORT_POLICY,
        lease,
        NOW + timedelta(seconds=2),
        NOW + timedelta(seconds=3),
    )
    assert result.candidate is not None
    publication_intent = RawPublicationIntent(
        publication_id="publication-edinet-1",
        task_id="task-1",
        logical_request_id=logical.logical_request_id,
        physical_attempt_id=attempt.physical_attempt_id,
        source_id="edinet",
        operation="document-list",
        content_sha256=result.candidate.sha256,
        byte_count=len(body),
        media_type="application/json",
        encoding="utf-8",
        raw_schema_id="edinet-document-list-raw",
        raw_schema_version=1,
        publication_generation=lease.generation,
    )
    published = publish_source_candidate(
        RawArtifactPublisher(runs, repository),
        result.candidate,
        publication_intent,
        lease,
        NOW + timedelta(seconds=4),
        NOW + timedelta(seconds=5),
        adapter,
    )
    return repository, published, runs


def test_edinet_document_list_runs_through_production_and_commits_exact_raw(tmp_path: Path) -> None:
    """Bind parsed EDINET output to exact raw bytes committed after validation."""
    body = FIXTURE.read_bytes()
    repository, published, runs = _run(tmp_path, body)

    assert published.value.documents[0].document_id == "SYNTHETIC001"
    assert published.reference.content_sha256 == hashlib.sha256(body).hexdigest()
    bundle = runs / "task-1" / published.reference.relative_path
    assert (bundle / "body.bin").read_bytes() == body
    assert repository.committed_raw_references("logical-edinet-1") == (published.reference,)


def test_edinet_parse_failure_never_commits_raw_bundle(tmp_path: Path) -> None:
    """Keep a transport-valid but source-invalid response out of committed raw."""
    with pytest.raises(EdinetDocumentListParseError, match="^edinet_document_list_invalid$"):
        _run(tmp_path, b'{"metadata":null,"results":[]}')

    assert not list((tmp_path / "runs" / "task-1" / ".staging" / "acquisitions").iterdir())
    assert not (tmp_path / "runs" / "task-1" / "acquisitions").exists()


def test_publication_bridge_rejects_a_different_source_adapter(tmp_path: Path) -> None:
    """Do not bind EDINET raw metadata to a parser claiming another source."""

    class WrongSourceAdapter(EdinetDocumentListAdapter):
        @property
        def source_id(self) -> str:
            return "wrong-source"

    body = FIXTURE.read_bytes()
    runtime = tmp_path / ".runtime"
    runtime.mkdir(mode=0o700)
    runs = tmp_path / "runs"
    runs.mkdir(mode=0o700)
    repository = initialize_runtime_storage(runtime)
    lease = repository.acquire_lease("owner-a", NOW, RuntimeLeasePolicy())
    candidate = TemporaryRawCandidate(
        "attempt-edinet-1", body, hashlib.sha256(body).hexdigest(), "application/json", "utf-8"
    )
    intent = RawPublicationIntent(
        publication_id="publication-edinet-1",
        task_id="task-1",
        logical_request_id="logical-edinet-1",
        physical_attempt_id="attempt-edinet-1",
        source_id="edinet",
        operation="document-list",
        content_sha256=candidate.sha256,
        byte_count=len(body),
        media_type="application/json",
        encoding="utf-8",
        raw_schema_id="edinet-document-list-raw",
        raw_schema_version=1,
        publication_generation=lease.generation,
    )

    with pytest.raises(ValueError, match="^source_adapter_intent_mismatch$"):
        publish_source_candidate(
            RawArtifactPublisher(runs, repository), candidate, intent, lease, NOW, NOW, WrongSourceAdapter()
        )

    assert list(runs.iterdir()) == []
