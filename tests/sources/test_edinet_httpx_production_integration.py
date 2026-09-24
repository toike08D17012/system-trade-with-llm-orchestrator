"""Offline integration of EDINET credential, HTTPX, and production coordination."""

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.external_requests import GateScope
from stock_research_llm_orchestrator.requests.production import (
    GateKeys,
    GateLimit,
    HierarchicalGatePolicy,
    ProductionCachePolicy,
    ProductionLogicalRequest,
    ProductionPhysicalAttempt,
    QueuePolicy,
    RuntimeLeasePolicy,
)
from stock_research_llm_orchestrator.requests.storage import initialize_runtime_storage
from stock_research_llm_orchestrator.requests.transport import ProductionTransportCoordinator, TransportValidationPolicy
from stock_research_llm_orchestrator.sources import (
    EdinetDocumentListAdapter,
    EdinetHttpClientPolicy,
    SourceParameter,
    build_httpx_edinet_transport,
)


NOW = datetime(2026, 9, 24, tzinfo=UTC)
CANARY = "dummy-edinet-production-key-do-not-persist"
FIXTURE = Path(__file__).parents[1] / "fixtures/sources/edinet/document-list.json"


def test_production_coordinator_uses_httpx_without_persisting_key(tmp_path: Path) -> None:
    """Carry exact provider bytes through the real send boundary without durable key data."""
    body = FIXTURE.read_bytes()
    credential = tmp_path / "edinet-api-key"
    credential.write_text(CANARY)
    credential.chmod(0o600)
    observed_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed_urls.append(str(request.url))
        return httpx.Response(200, headers={"Content-Type": "application/json; charset=utf-8"}, content=body)

    runtime = tmp_path / ".runtime"
    runtime.mkdir(mode=0o700)
    repository = initialize_runtime_storage(runtime)
    lease = repository.acquire_lease("owner-a", NOW, RuntimeLeasePolicy())
    intent = EdinetDocumentListAdapter().build_intent(
        "document-list",
        (SourceParameter(name="date", value="2026-09-22"), SourceParameter(name="type", value="2")),
    )
    logical = ProductionLogicalRequest(
        logical_request_id="logical-edinet-httpx-1",
        task_id="task-1",
        source_id="edinet",
        operation="document-list",
        request_fingerprint=hashlib.sha256(intent.model_dump_json().encode()).hexdigest(),
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
        physical_attempt_id="attempt-edinet-httpx-1",
        logical_request_id=logical.logical_request_id,
        sequence_number=1,
        lease_generation=lease.generation,
        created_at=(NOW + timedelta(seconds=2)).isoformat(),
    )
    callback = build_httpx_edinet_transport(
        intent,
        credential,
        EdinetHttpClientPolicy(
            max_response_bytes=len(body),
            connect_timeout_seconds=1,
            read_timeout_seconds=2,
            write_timeout_seconds=3,
            pool_timeout_seconds=4,
        ),
        transport=httpx.MockTransport(handler),
    )
    result = ProductionTransportCoordinator(repository, callback, lambda: "permit-edinet-httpx-1").execute(
        intent.to_transport_request(logical.logical_request_id, attempt.physical_attempt_id),
        attempt,
        "reservation-edinet-httpx-1",
        GateKeys(
            egress="default-egress",
            provider="edinet-api",
            origin="api.edinet-fsa.go.jp",
            credential="edinet-api-key",
            operation="document-list",
            task="task-1",
            role="source-acquisition",
        ),
        HierarchicalGatePolicy(
            limits={
                scope: GateLimit(
                    max_concurrency=1,
                    min_interval_seconds=60,
                    requests_per_window=1,
                    window_seconds=60,
                )
                for scope in GateScope
            }
        ),
        TransportValidationPolicy(
            max_response_bytes=len(body),
            allowed_media_types=("application/json",),
            allowed_encodings=("utf-8",),
        ),
        lease,
        NOW + timedelta(seconds=2),
        NOW + timedelta(seconds=3),
    )

    assert result.status == "succeeded"
    assert result.candidate is not None
    assert result.candidate.body == body
    assert result.candidate.sha256 == hashlib.sha256(body).hexdigest()
    assert len(observed_urls) == 1
    assert f"Subscription-Key={CANARY}" in observed_urls[0]
    for path in runtime.rglob("*"):
        if path.is_file():
            assert CANARY.encode() not in path.read_bytes()
