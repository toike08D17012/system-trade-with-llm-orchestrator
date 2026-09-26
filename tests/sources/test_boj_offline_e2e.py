"""Offline BOJ E2E through HTTPX, production coordination, and publication."""

import hashlib
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx

from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.external_requests import GateScope
from stock_research_llm_orchestrator.requests.production import (
    GateKeys,
    GateLimit,
    HierarchicalGatePolicy,
    LogicalResultOutcome,
    ProductionCachePolicy,
    ProductionLogicalRequest,
    ProductionPhysicalAttempt,
    QueuePolicy,
    RawPublicationIntent,
    RuntimeLeasePolicy,
)
from stock_research_llm_orchestrator.requests.raw_artifacts import RawArtifactPublisher
from stock_research_llm_orchestrator.requests.storage import initialize_runtime_storage
from stock_research_llm_orchestrator.requests.transport import ProductionTransportCoordinator, TransportValidationPolicy
from stock_research_llm_orchestrator.sources import (
    BojFxCodeAdapter,
    BojHttpClientPolicy,
    BojPhysicalTransport,
    SourceParameter,
    publish_source_candidate,
)


NOW = datetime(2026, 9, 24, tzinfo=UTC)
FIXTURE = Path(__file__).parents[1] / "fixtures/sources/boj/fxerd04.json"


def test_boj_fx_runs_through_production_and_publishes_exact_raw(tmp_path: Path) -> None:
    """Preserve official raw identity and an explicit missing daily observation."""
    body = FIXTURE.read_bytes()
    runtime = tmp_path / ".runtime"
    runtime.mkdir(mode=0o700)
    runs = tmp_path / "runs"
    runs.mkdir(mode=0o700)
    repository = initialize_runtime_storage(runtime)
    lease = repository.acquire_lease("owner-a", NOW, RuntimeLeasePolicy())
    adapter = BojFxCodeAdapter()
    intent = adapter.build_intent(
        "fx-daily-code",
        (
            SourceParameter(name="code", value="FXERD04"),
            SourceParameter(name="db", value="FM08"),
            SourceParameter(name="end_date", value="202609"),
            SourceParameter(name="format", value="json"),
            SourceParameter(name="lang", value="en"),
            SourceParameter(name="start_date", value="202609"),
        ),
    )
    logical = ProductionLogicalRequest(
        logical_request_id="logical-boj-fx-1",
        task_id="task-1",
        source_id="boj",
        operation="fx-daily-code",
        request_fingerprint=hashlib.sha256(intent.model_dump_json().encode()).hexdigest(),
        source_approval_version=1,
        source_profile_version=1,
        credential_scope_alias=None,
        egress_scope="default-egress",
        created_at=NOW.isoformat(),
    )
    repository.admit_logical_request(
        logical, "boj-stat-search-api", ProductionCachePolicy(applicable=False), lease, NOW, QueuePolicy()
    )
    assert repository.claim_next_queued("boj-stat-search-api", lease, NOW + timedelta(seconds=1)) is not None
    attempt = ProductionPhysicalAttempt(
        physical_attempt_id="attempt-boj-fx-1",
        logical_request_id=logical.logical_request_id,
        sequence_number=1,
        lease_generation=lease.generation,
        created_at=(NOW + timedelta(seconds=2)).isoformat(),
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"Content-Type": "application/json; charset=utf-8"}, content=body)

    callback = BojPhysicalTransport(
        intent,
        BojHttpClientPolicy(
            connect_timeout_seconds=1,
            read_timeout_seconds=2,
            write_timeout_seconds=1,
            pool_timeout_seconds=1,
        ),
        httpx.MockTransport(handler),
    )
    coordinator = ProductionTransportCoordinator(repository, callback, lambda: "permit-boj-fx-1")
    result = coordinator.execute_exchange(
        intent.to_transport_request(logical.logical_request_id, attempt.physical_attempt_id),
        attempt,
        "reservation-boj-fx-1",
        GateKeys(
            egress="default-egress",
            provider="boj-stat-search-api",
            origin="www.stat-search.boj.or.jp",
            credential="anonymous",
            operation="fx-daily-code",
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
            allowed_media_types=("application/json",),
            allowed_encodings=("utf-8",),
        ),
        lease,
        NOW + timedelta(seconds=2),
        NOW + timedelta(seconds=3),
    )
    assert repository.logical_result(logical.logical_request_id) is None
    assert result.candidate is not None
    publication_intent = RawPublicationIntent(
        publication_id="publication-boj-fx-1",
        task_id="task-1",
        logical_request_id=logical.logical_request_id,
        physical_attempt_id=attempt.physical_attempt_id,
        source_id="boj",
        operation="fx-daily-code",
        content_sha256=result.candidate.sha256,
        byte_count=len(body),
        media_type="application/json",
        encoding="utf-8",
        raw_schema_id="boj-fx-daily-code-raw",
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

    coordinator.finalize_logical_request(
        logical.logical_request_id, LogicalResultOutcome.SUCCEEDED, lease, NOW + timedelta(seconds=6), None
    )
    assert tuple(item.value for item in published.value.observations) == (Decimal("147.25"), None, Decimal("148"))
    assert published.reference.content_sha256 == hashlib.sha256(body).hexdigest()
    assert (runs / "task-1" / published.reference.relative_path / "body.bin").read_bytes() == body
    assert repository.committed_raw_references(logical.logical_request_id) == (published.reference,)
