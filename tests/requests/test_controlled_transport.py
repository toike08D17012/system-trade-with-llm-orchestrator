import hashlib
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

import stock_research_llm_orchestrator.requests as requests_package
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.external_requests import GateScope
from stock_research_llm_orchestrator.requests.production import (
    GateKeys,
    GateLimit,
    HierarchicalGatePolicy,
    ProductionCachePolicy,
    ProductionLogicalRequest,
    ProductionPhysicalAttempt,
    QueuePolicy,
    RuntimeLease,
    RuntimeLeasePolicy,
)
from stock_research_llm_orchestrator.requests.storage import (
    ProductionRequestRepository,
    RuntimeStorageError,
    initialize_runtime_storage,
)
from stock_research_llm_orchestrator.requests.transport import (
    PhysicalTransportRequest,
    ProductionTransportCoordinator,
    TransportExecutionResult,
    TransportValidationPolicy,
    UntrustedTransportResponse,
)


NOW = datetime(2026, 9, 22, tzinfo=UTC)
KEYS = GateKeys(
    egress="default",
    provider="provider-a",
    origin="example-com",
    credential="anonymous",
    operation="history",
    task="task-1",
    role="researcher",
)
LIMIT = GateLimit(max_concurrency=1, min_interval_seconds=0, requests_per_window=10, window_seconds=60)
GATE_POLICY = HierarchicalGatePolicy(limits={scope: LIMIT for scope in GateScope})
TRANSPORT_POLICY = TransportValidationPolicy(
    max_response_bytes=1024,
    allowed_media_types=("application/json",),
    allowed_encodings=("utf-8",),
)


def _prepared(
    tmp_path: Path,
) -> tuple[ProductionRequestRepository, RuntimeLease, PhysicalTransportRequest, ProductionPhysicalAttempt]:
    root = tmp_path / ".runtime"
    root.mkdir(mode=0o700)
    repository = initialize_runtime_storage(root)
    lease = repository.acquire_lease("owner-a", NOW, RuntimeLeasePolicy())
    logical = ProductionLogicalRequest(
        logical_request_id="logical-1",
        task_id="task-1",
        source_id="fixture",
        operation="history",
        request_fingerprint="a" * 64,
        source_approval_version=1,
        source_profile_version=1,
        credential_scope_alias=None,
        egress_scope="default",
        created_at=NOW.isoformat(),
    )
    repository.admit_logical_request(
        logical,
        "provider-a",
        ProductionCachePolicy(applicable=False),
        lease,
        NOW,
        QueuePolicy(),
    )
    assert repository.claim_next_queued("provider-a", lease, NOW + timedelta(seconds=1)) is not None
    request = PhysicalTransportRequest(
        logical_request_id="logical-1",
        physical_attempt_id="attempt-1",
        origin="example-com",
        operation="history",
        resource_key="fixture-response",
    )
    attempt = ProductionPhysicalAttempt(
        physical_attempt_id="attempt-1",
        logical_request_id="logical-1",
        sequence_number=1,
        lease_generation=1,
        created_at=(NOW + timedelta(seconds=2)).isoformat(),
    )
    return repository, lease, request, attempt


def _response(
    status: int = 200,
    *,
    body: bytes = b'{"value":1}',
    origin: str = "example-com",
    retry_after: float | None = None,
) -> UntrustedTransportResponse:
    return UntrustedTransportResponse(
        status_code=status,
        body=body,
        media_type="application/json",
        encoding="utf-8",
        final_origin=origin,
        redirected=False,
        retry_after_seconds=retry_after,
    )


def _execute(
    tmp_path: Path,
    callback: Callable[[PhysicalTransportRequest], UntrustedTransportResponse],
) -> tuple[ProductionRequestRepository, TransportExecutionResult]:
    repository, lease, request, attempt = _prepared(tmp_path)
    coordinator = ProductionTransportCoordinator(repository, callback, lambda: "permit-1")
    result = coordinator.execute(
        request,
        attempt,
        "reservation-1",
        KEYS,
        GATE_POLICY,
        TRANSPORT_POLICY,
        lease,
        NOW + timedelta(seconds=2),
        NOW + timedelta(seconds=3),
    )
    return repository, result


def test_success_returns_validated_temporary_raw_candidate(tmp_path: Path) -> None:
    """Hash the exact validated bytes without source-specific parsing."""
    repository, result = _execute(tmp_path, lambda request: _response())

    assert result.status == "succeeded"
    assert result.candidate is not None
    assert result.candidate.sha256 == hashlib.sha256(b'{"value":1}').hexdigest()
    assert repository.logical_request_state("logical-1") == "succeeded"


@pytest.mark.parametrize(
    ("callback", "status", "reason", "attempt_state"),
    [
        (lambda request: _response(503, body=b""), "failed", "provider_error", "failed"),
        (lambda request: _response(429, body=b"", retry_after=5), "failed", "rate_limited", "failed"),
        (lambda request: _response(origin="evil-example"), "failed", "invalid_transport_response", "failed"),
    ],
)
def test_known_failures_are_sanitized_and_durable(
    tmp_path: Path,
    callback: Callable[[PhysicalTransportRequest], UntrustedTransportResponse],
    status: str,
    reason: str,
    attempt_state: str,
) -> None:
    """Classify provider and common-validation failures without returning raw bytes."""
    repository, result = _execute(tmp_path, callback)

    assert result.status == status
    assert result.reason_code == reason
    assert result.candidate is None
    assert repository.physical_attempt_state("attempt-1") == (attempt_state, 1)


def test_callback_exception_is_unknown_and_redacted(tmp_path: Path) -> None:
    """Treat a possibly-sent callback exception conservatively without leaking it."""

    def raise_secret(request: PhysicalTransportRequest) -> UntrustedTransportResponse:
        raise RuntimeError("secret-token-value")

    repository, result = _execute(tmp_path, raise_secret)

    assert result.status == "unknown"
    assert result.reason_code == "transport_outcome_unknown"
    assert "secret" not in repr(result)
    assert repository.physical_attempt_state("attempt-1") == ("unknown", 1)


def test_stale_lease_never_calls_transport(tmp_path: Path) -> None:
    """Stop before callback when the coordinator no longer owns the runtime."""
    repository, lease, request, attempt = _prepared(tmp_path)
    repository.release_lease(lease, NOW + timedelta(seconds=2))
    called = False

    def callback(request: PhysicalTransportRequest) -> UntrustedTransportResponse:
        nonlocal called
        called = True
        return _response()

    coordinator = ProductionTransportCoordinator(repository, callback, lambda: "permit-1")
    with pytest.raises(RuntimeStorageError, match="stale_runtime_lease"):
        coordinator.execute(
            request,
            attempt,
            "reservation-1",
            KEYS,
            GATE_POLICY,
            TRANSPORT_POLICY,
            lease,
            NOW + timedelta(seconds=3),
            NOW + timedelta(seconds=4),
        )

    assert called is False


def test_permit_factory_failure_never_calls_transport_or_leaks_error(tmp_path: Path) -> None:
    """Convert a pre-send capability failure into a sanitized durable failure."""
    repository, lease, request, attempt = _prepared(tmp_path)
    called = False

    def callback(request: PhysicalTransportRequest) -> UntrustedTransportResponse:
        nonlocal called
        called = True
        return _response()

    def fail_permit() -> str:
        raise RuntimeError("secret-permit-detail")

    coordinator = ProductionTransportCoordinator(repository, callback, fail_permit)
    result = coordinator.execute(
        request,
        attempt,
        "reservation-1",
        KEYS,
        GATE_POLICY,
        TRANSPORT_POLICY,
        lease,
        NOW + timedelta(seconds=2),
        NOW + timedelta(seconds=3),
    )

    assert called is False
    assert result.reason_code == "permit_issuance_failed"
    assert "secret" not in repr(result)
    assert repository.physical_attempt_state("attempt-1") == ("failed", 1)


def test_transport_capability_is_not_publicly_exported() -> None:
    """Expose the coordinator API without exposing its transport or permit constructors."""
    assert not hasattr(requests_package, "TransportPermit")
    assert not hasattr(requests_package, "ControlledTransport")
