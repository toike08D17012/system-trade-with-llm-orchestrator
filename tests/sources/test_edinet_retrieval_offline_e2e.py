"""Offline EDINET XBRL retrieval through production coordination and publication."""

import hashlib
import io
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
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
    TransportValidationPolicy,
)
from stock_research_llm_orchestrator.sources import (
    EdinetHttpClientPolicy,
    EdinetXbrlDocument,
    EdinetXbrlDocumentAdapter,
    EdinetXbrlFactParseError,
    PublishedSourceResult,
    SourceParameter,
    build_httpx_edinet_transport,
    publish_source_candidate,
)


NOW = datetime(2026, 9, 23, tzinfo=UTC)
CANARY = "dummy-edinet-retrieval-key-do-not-persist"
XBRL = b"""<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance"
 xmlns:iso4217="http://www.xbrl.org/2003/iso4217" xmlns:jp="https://example.invalid/synthetic">
 <xbrli:context id="CurrentYear">
  <xbrli:entity><xbrli:identifier scheme="https://example.invalid/entity">ENTITY</xbrli:identifier></xbrli:entity>
  <xbrli:period><xbrli:startDate>2025-04-01</xbrli:startDate><xbrli:endDate>2026-03-31</xbrli:endDate></xbrli:period>
 </xbrli:context>
 <xbrli:unit id="JPY"><xbrli:measure>iso4217:JPY</xbrli:measure></xbrli:unit>
 <jp:Revenue contextRef="CurrentYear" unitRef="JPY" decimals="-6">123000000</jp:Revenue>
</xbrli:xbrl>"""


def _archive(xbrl: bytes) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("XBRL/PublicDoc/synthetic.xbrl", xbrl)
    return output.getvalue()


def _run(
    tmp_path: Path, body: bytes
) -> tuple[ProductionRequestRepository, PublishedSourceResult[EdinetXbrlDocument], Path]:
    runtime = tmp_path / ".runtime"
    runtime.mkdir(mode=0o700)
    runs = tmp_path / "runs"
    runs.mkdir(mode=0o700)
    repository = initialize_runtime_storage(runtime)
    lease = repository.acquire_lease("owner-a", NOW, RuntimeLeasePolicy())
    adapter = EdinetXbrlDocumentAdapter()
    source_intent = adapter.build_intent(
        "document-retrieval",
        (SourceParameter(name="document_id", value="SYNTHETIC001"), SourceParameter(name="type", value="1")),
    )
    credential = tmp_path / "edinet-api-key"
    credential.write_text(CANARY)
    credential.chmod(0o600)
    logical = ProductionLogicalRequest(
        logical_request_id="logical-edinet-xbrl-1",
        task_id="task-1",
        source_id="edinet",
        operation="document-retrieval",
        request_fingerprint=hashlib.sha256(source_intent.model_dump_json().encode()).hexdigest(),
        source_approval_version=1,
        source_profile_version=1,
        credential_scope_alias="edinet-api-key",
        egress_scope="default-egress",
        created_at=NOW.isoformat(),
    )
    repository.admit_logical_request(
        logical, "edinet-api", ProductionCachePolicy(applicable=False), lease, NOW, QueuePolicy()
    )
    assert repository.claim_next_queued("edinet-api", lease, NOW + timedelta(seconds=1)) is not None
    attempt = ProductionPhysicalAttempt(
        physical_attempt_id="attempt-edinet-xbrl-1",
        logical_request_id=logical.logical_request_id,
        sequence_number=1,
        lease_generation=lease.generation,
        created_at=(NOW + timedelta(seconds=2)).isoformat(),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/documents/SYNTHETIC001"
        assert request.url.params["type"] == "1"
        assert request.url.params["Subscription-Key"] == CANARY
        return httpx.Response(200, headers={"Content-Type": "application/octet-stream"}, content=body)

    callback = build_httpx_edinet_transport(
        source_intent,
        credential,
        EdinetHttpClientPolicy(
            connect_timeout_seconds=1,
            read_timeout_seconds=10,
            write_timeout_seconds=1,
            pool_timeout_seconds=1,
        ),
        transport=httpx.MockTransport(handler),
    )
    result = ProductionTransportCoordinator(repository, callback, lambda: "permit-edinet-xbrl-1").execute(
        source_intent.to_transport_request(logical.logical_request_id, attempt.physical_attempt_id),
        attempt,
        "reservation-edinet-xbrl-1",
        GateKeys(
            egress="default-egress",
            provider="edinet-api",
            origin="api.edinet-fsa.go.jp",
            credential="edinet-api-key",
            operation="document-retrieval",
            task="task-1",
            role="source-acquisition",
        ),
        HierarchicalGatePolicy(
            limits={
                scope: GateLimit(max_concurrency=1, min_interval_seconds=60, requests_per_window=1, window_seconds=60)
                for scope in GateScope
            }
        ),
        TransportValidationPolicy(
            allowed_media_types=("application/octet-stream",),
            allowed_encodings=("binary",),
        ),
        lease,
        NOW + timedelta(seconds=2),
        NOW + timedelta(seconds=3),
    )
    assert result.candidate is not None
    intent = RawPublicationIntent(
        publication_id="publication-edinet-xbrl-1",
        task_id="task-1",
        logical_request_id=logical.logical_request_id,
        physical_attempt_id=attempt.physical_attempt_id,
        source_id="edinet",
        operation="document-retrieval",
        content_sha256=result.candidate.sha256,
        byte_count=len(body),
        media_type="application/octet-stream",
        encoding="binary",
        raw_schema_id="edinet-xbrl-zip-raw",
        raw_schema_version=1,
        publication_generation=lease.generation,
    )
    published = publish_source_candidate(
        RawArtifactPublisher(runs, repository),
        result.candidate,
        intent,
        lease,
        NOW + timedelta(seconds=4),
        NOW + timedelta(seconds=5),
        adapter,
    )
    for root in (runtime, runs):
        for path in root.rglob("*"):
            if path.is_file():
                assert CANARY.encode() not in path.read_bytes()
    return repository, published, runs


def test_xbrl_retrieval_commits_only_after_fact_extraction(tmp_path: Path) -> None:
    """Bind extracted source-native facts to the exact committed ZIP."""
    body = _archive(XBRL)
    repository, published, runs = _run(tmp_path, body)

    assert published.value.facts.facts[0].concept_local_name == "Revenue"
    assert published.value.facts.facts[0].value == "123000000"
    assert published.reference.content_sha256 == hashlib.sha256(body).hexdigest()
    assert (runs / "task-1" / published.reference.relative_path / "body.bin").read_bytes() == body
    assert repository.committed_raw_references("logical-edinet-xbrl-1") == (published.reference,)


def test_fact_failure_never_commits_an_otherwise_valid_zip(tmp_path: Path) -> None:
    """Reject raw publication when ZIP inventory passes but XBRL semantics fail."""
    malformed = XBRL.replace(b'contextRef="CurrentYear"', b'contextRef="Missing"')
    with pytest.raises(EdinetXbrlFactParseError, match="^edinet_xbrl_facts_invalid$"):
        _run(tmp_path, _archive(malformed))

    assert not (tmp_path / "runs" / "task-1" / "acquisitions").exists()
