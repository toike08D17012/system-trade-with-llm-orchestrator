"""Offline integration of EDINET credential, HTTPX, and production coordination."""

import hashlib
from collections.abc import Callable
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
    RuntimeLeasePolicy,
)
from stock_research_llm_orchestrator.requests.storage import ProductionRequestRepository, initialize_runtime_storage
from stock_research_llm_orchestrator.requests.transport import (
    ProductionTransportCoordinator,
    TransportExecutionResult,
    TransportValidationPolicy,
)
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


def _raise_timeout(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout(CANARY, request=request)


@pytest.mark.parametrize(
    ("scenario", "response_factory", "wire_limit", "expected_status", "expected_reason", "expected_attempt_state"),
    [
        (
            "provider-error",
            lambda _request: httpx.Response(500, headers={"Content-Type": "application/json"}, content=b"{}"),
            32,
            "failed",
            "provider_error",
            "failed",
        ),
        (
            "rate-limited",
            lambda _request: httpx.Response(
                429,
                headers={"Content-Type": "application/json", "Retry-After": "60"},
                content=b"{}",
            ),
            32,
            "failed",
            "rate_limited",
            "failed",
        ),
        (
            "redirect",
            lambda _request: httpx.Response(302, headers={"Location": "https://example.invalid/other"}),
            32,
            "unknown",
            "transport_outcome_unknown",
            "unknown",
        ),
        (
            "response-too-large",
            lambda _request: httpx.Response(200, headers={"Content-Type": "application/json"}, content=b"x" * 33),
            32,
            "unknown",
            "transport_outcome_unknown",
            "unknown",
        ),
        (
            "timeout",
            _raise_timeout,
            32,
            "unknown",
            "transport_outcome_unknown",
            "unknown",
        ),
    ],
)
def test_production_coordinator_records_http_failures_without_persisting_key(
    tmp_path: Path,
    scenario: str,
    response_factory: Callable[[httpx.Request], httpx.Response],
    wire_limit: int,
    expected_status: str,
    expected_reason: str,
    expected_attempt_state: str,
) -> None:
    """Classify received provider errors separately from uncertain post-send failures."""
    result, repository, runtime = _execute_failure_case(tmp_path / scenario, response_factory, wire_limit)

    assert result.status == expected_status
    assert result.reason_code == expected_reason
    assert result.candidate is None
    assert repository.physical_attempt_state("attempt-edinet-httpx-failure") == (expected_attempt_state, 1)
    for path in runtime.rglob("*"):
        if path.is_file():
            assert CANARY.encode() not in path.read_bytes()


def _execute_failure_case(
    root: Path,
    response_factory: Callable[[httpx.Request], httpx.Response],
    wire_limit: int,
) -> tuple[TransportExecutionResult, ProductionRequestRepository, Path]:
    root.mkdir()
    credential = root / "edinet-api-key"
    credential.write_text(CANARY)
    credential.chmod(0o600)
    runtime = root / ".runtime"
    runtime.mkdir(mode=0o700)
    repository = initialize_runtime_storage(runtime)
    lease = repository.acquire_lease("owner-a", NOW, RuntimeLeasePolicy())
    intent = EdinetDocumentListAdapter().build_intent(
        "document-list",
        (SourceParameter(name="date", value="2026-09-22"), SourceParameter(name="type", value="2")),
    )
    logical = ProductionLogicalRequest(
        logical_request_id="logical-edinet-httpx-failure",
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
        physical_attempt_id="attempt-edinet-httpx-failure",
        logical_request_id=logical.logical_request_id,
        sequence_number=1,
        lease_generation=lease.generation,
        created_at=(NOW + timedelta(seconds=2)).isoformat(),
    )
    callback = build_httpx_edinet_transport(
        intent,
        credential,
        EdinetHttpClientPolicy(
            max_response_bytes=wire_limit,
            connect_timeout_seconds=1,
            read_timeout_seconds=2,
            write_timeout_seconds=3,
            pool_timeout_seconds=4,
        ),
        transport=httpx.MockTransport(response_factory),
    )
    result = ProductionTransportCoordinator(repository, callback, lambda: "permit-edinet-httpx-failure").execute(
        intent.to_transport_request(logical.logical_request_id, attempt.physical_attempt_id),
        attempt,
        "reservation-edinet-httpx-failure",
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
            max_response_bytes=wire_limit,
            allowed_media_types=("application/json",),
            allowed_encodings=("utf-8",),
        ),
        lease,
        NOW + timedelta(seconds=2),
        NOW + timedelta(seconds=3),
    )
    return result, repository, runtime
