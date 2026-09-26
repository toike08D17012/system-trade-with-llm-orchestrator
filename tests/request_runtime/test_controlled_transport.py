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
    LogicalResultOutcome,
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
    ReceivedResponseValidationError,
    TransportExecutionResult,
    TransportValidationPolicy,
    UntrustedTransportResponse,
    _validate_response,
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
    allowed_media_types=("application/json",),
    allowed_encodings=("utf-8",),
)


def test_unbounded_policy_preserves_format_validation() -> None:
    """All sources accept arbitrary body sizes while still rejecting wrong media types."""
    request = PhysicalTransportRequest(
        logical_request_id="logical-1",
        physical_attempt_id="attempt-1",
        origin="example-com",
        operation="history",
        resource_key="fixture-response",
    )
    response = UntrustedTransportResponse(
        status_code=200,
        body=b"x" * 1025,
        media_type="application/json",
        encoding="utf-8",
        final_origin="example-com",
        redirected=False,
    )
    assert TRANSPORT_POLICY.max_response_bytes is None
    _validate_response(request, response, TRANSPORT_POLICY)
    with pytest.raises(RuntimeStorageError, match="response_media_type_rejected"):
        _validate_response(request, response.model_copy(update={"media_type": "text/html"}), TRANSPORT_POLICY)


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


def test_multiple_exchanges_remain_active_until_explicit_finalization(tmp_path: Path) -> None:
    """Keep one logical request open across ordered, independently gated sends."""
    repository, lease, first_request, first_attempt = _prepared(tmp_path)
    responses = iter((_response(503, body=b'{"retry":1}'), _response(body=b'{"value":2}')))
    permits = iter(("permit-1", "permit-2"))
    coordinator = ProductionTransportCoordinator(repository, lambda _request: next(responses), lambda: next(permits))

    first = coordinator.execute_exchange(
        first_request,
        first_attempt,
        "reservation-1",
        KEYS,
        GATE_POLICY,
        TRANSPORT_POLICY,
        lease,
        NOW + timedelta(seconds=2),
        NOW + timedelta(seconds=3),
    )
    second_attempt = repository.reserve_next_physical_attempt(
        "attempt-2", "logical-1", lease, NOW + timedelta(seconds=4)
    )
    second_request = first_request.model_copy(update={"physical_attempt_id": "attempt-2"})
    second = coordinator.execute_exchange(
        second_request,
        second_attempt,
        "reservation-2",
        KEYS,
        GATE_POLICY,
        TRANSPORT_POLICY,
        lease,
        NOW + timedelta(seconds=4),
        NOW + timedelta(seconds=5),
    )

    assert first.status == "failed" and first.candidate is not None
    assert second.status == "succeeded" and second.candidate is not None
    assert second_attempt.sequence_number == 2
    assert repository.logical_request_state("logical-1") == "queued"
    coordinator.finalize_logical_request(
        "logical-1", LogicalResultOutcome.SUCCEEDED, lease, NOW + timedelta(seconds=6), None
    )
    assert repository.logical_request_state("logical-1") == "succeeded"


def test_unknown_exchange_prevents_another_attempt(tmp_path: Path) -> None:
    """Never allocate another send after the provider outcome became indeterminate."""
    repository, lease, request, attempt = _prepared(tmp_path)

    def fail_after_send(_request: PhysicalTransportRequest) -> UntrustedTransportResponse:
        raise RuntimeError("wire outcome unknown")

    result = ProductionTransportCoordinator(repository, fail_after_send, lambda: "permit-1").execute_exchange(
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

    assert result.status == "unknown"
    with pytest.raises(RuntimeStorageError, match="physical_attempt_already_unknown"):
        repository.reserve_next_physical_attempt("attempt-2", "logical-1", lease, NOW + timedelta(seconds=4))


def test_ephemeral_callback_runs_after_permit_and_returns_validated_opaque_response(tmp_path: Path) -> None:
    """Bridge a dynamic request without adding its envelope or response to durable models."""
    repository, lease, request, attempt = _prepared(tmp_path)
    envelope = object()
    provider_response = object()
    observed: list[tuple[PhysicalTransportRequest, object]] = []
    clock_observations: list[str] = []

    def callback(
        controlled_request: PhysicalTransportRequest, controlled_envelope: object
    ) -> tuple[UntrustedTransportResponse, object]:
        observed.append((controlled_request, controlled_envelope))
        return _response(503, body=b'{"fallback":true}'), provider_response

    def completion_clock() -> datetime:
        assert observed == [(request, envelope)]
        clock_observations.append("after_callback")
        return NOW + timedelta(seconds=3)

    result = ProductionTransportCoordinator(
        repository, lambda _request: _response(), lambda: "permit-1"
    ).execute_exchange_with_callback(
        request,
        attempt,
        "reservation-1",
        KEYS,
        GATE_POLICY,
        TRANSPORT_POLICY,
        lease,
        NOW + timedelta(seconds=2),
        completion_clock,
        envelope,
        callback,
    )

    assert observed == [(request, envelope)]
    assert clock_observations == ["after_callback"]
    assert result.status_code == 503
    assert result.provider_response is provider_response
    assert result.execution.status == "failed"
    assert result.execution.candidate is not None
    assert "object at" not in repr(result)


def test_received_response_validation_failure_is_known_and_allows_safe_finalization(tmp_path: Path) -> None:
    """Do not classify a locally rejected, already received response as an unknown send."""
    repository, lease, request, attempt = _prepared(tmp_path)

    def reject_received_response(
        controlled_request: PhysicalTransportRequest, controlled_envelope: object
    ) -> tuple[UntrustedTransportResponse, object]:
        raise ReceivedResponseValidationError("provider body is invalid")

    result = ProductionTransportCoordinator(
        repository, lambda _request: _response(), lambda: "permit-1"
    ).execute_exchange_with_callback(
        request,
        attempt,
        "reservation-1",
        KEYS,
        GATE_POLICY,
        TRANSPORT_POLICY,
        lease,
        NOW + timedelta(seconds=2),
        lambda: NOW + timedelta(seconds=3),
        object(),
        reject_received_response,
    )

    assert result.execution.status == "failed"
    assert result.execution.reason_code == "invalid_transport_response"
    assert result.execution.candidate is None
    assert result.status_code is None
    assert repository.physical_attempt_state("attempt-1") == ("failed", 1)
    assert repository.logical_request_state("logical-1") == "queued"
    assert (
        repository.reserve_next_physical_attempt(
            "attempt-2", "logical-1", lease, NOW + timedelta(seconds=4)
        ).sequence_number
        == 2
    )


def test_ephemeral_io_exception_remains_unknown(tmp_path: Path) -> None:
    """Keep genuine callback failures conservative when no response is known to exist."""
    repository, lease, request, attempt = _prepared(tmp_path)

    def fail_without_response(
        controlled_request: PhysicalTransportRequest, controlled_envelope: object
    ) -> tuple[UntrustedTransportResponse, object]:
        raise OSError("wire outcome unavailable")

    result = ProductionTransportCoordinator(
        repository, lambda _request: _response(), lambda: "permit-1"
    ).execute_exchange_with_callback(
        request,
        attempt,
        "reservation-1",
        KEYS,
        GATE_POLICY,
        TRANSPORT_POLICY,
        lease,
        NOW + timedelta(seconds=2),
        lambda: NOW + timedelta(seconds=3),
        object(),
        fail_without_response,
    )

    assert result.execution.status == "unknown"
    assert result.execution.reason_code == "transport_outcome_unknown"
    assert repository.physical_attempt_state("attempt-1") == ("unknown", 1)
