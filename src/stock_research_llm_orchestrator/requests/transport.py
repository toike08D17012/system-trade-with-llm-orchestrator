"""Permit-only production transport boundary with injectable synthetic I/O."""

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from pydantic import Field

from stock_research_llm_orchestrator.contracts.base import Identifier, StrictContractModel
from stock_research_llm_orchestrator.requests.production import (
    GateKeys,
    GateReservation,
    HierarchicalGatePolicy,
    LogicalResultOutcome,
    ProductionLogicalResult,
    ProductionPhysicalAttempt,
    RuntimeLease,
)
from stock_research_llm_orchestrator.requests.storage import ProductionRequestRepository, RuntimeStorageError


class PhysicalTransportRequest(StrictContractModel):
    """Credential-free request description passed to an internal transport."""

    logical_request_id: Identifier
    physical_attempt_id: Identifier
    origin: Identifier
    operation: Identifier
    resource_key: Identifier


class UntrustedTransportResponse(StrictContractModel):
    """Synthetic representation of untrusted common transport metadata."""

    status_code: int = Field(ge=100, le=599)
    body: bytes
    media_type: str = Field(min_length=1, max_length=128)
    encoding: str = Field(min_length=1, max_length=64)
    final_origin: Identifier
    redirected: bool
    retry_after_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class TransportValidationPolicy(StrictContractModel):
    """Provider-independent bounds checked before source parsing."""

    max_response_bytes: int = Field(ge=1)
    allowed_media_types: tuple[str, ...] = Field(min_length=1)
    allowed_encodings: tuple[str, ...] = Field(min_length=1)
    allowed_redirect_origins: tuple[Identifier, ...] = ()


SyntheticTransport = Callable[[PhysicalTransportRequest], UntrustedTransportResponse]


class _TransportCallbackError(RuntimeError):
    """Sanitized marker for a possibly-sent request with no response."""


@dataclass(frozen=True)
class TransportPermit:
    """Opaque one-shot capability issued by one coordinator instance."""

    permit_id: str
    physical_attempt_id: str
    logical_request_id: str
    lease_generation: int
    _authority_token: object = field(repr=False, compare=False)


@dataclass(frozen=True)
class TemporaryRawCandidate:
    """Validated ephemeral bytes awaiting Phase 6 durable staging."""

    physical_attempt_id: str
    body: bytes = field(repr=False)
    sha256: str
    media_type: str
    encoding: str


@dataclass(frozen=True)
class TransportExecutionResult:
    """Sanitized production control-path outcome."""

    status: Literal["succeeded", "failed", "unknown"]
    reason_code: str
    candidate: TemporaryRawCandidate | None = None


class _PermitAuthority:
    def __init__(self, permit_id_factory: Callable[[], str]) -> None:
        self._token = object()
        self._permit_id_factory = permit_id_factory
        self._active: dict[str, TransportPermit] = {}

    def issue(self, reservation: GateReservation) -> TransportPermit:
        permit_id = self._permit_id_factory()
        if permit_id in self._active:
            raise RuntimeStorageError("duplicate_transport_permit")
        permit = TransportPermit(
            permit_id=permit_id,
            physical_attempt_id=reservation.physical_attempt_id,
            logical_request_id=reservation.logical_request_id,
            lease_generation=reservation.lease_generation,
            _authority_token=self._token,
        )
        self._active[permit_id] = permit
        return permit

    def consume(self, permit: TransportPermit, request: PhysicalTransportRequest) -> None:
        active = self._active.pop(permit.permit_id, None)
        if (
            permit._authority_token is not self._token
            or active is not permit
            or permit.physical_attempt_id != request.physical_attempt_id
            or permit.logical_request_id != request.logical_request_id
        ):
            raise RuntimeStorageError("invalid_transport_permit")


class _ControlledTransport:
    def __init__(self, callback: SyntheticTransport, authority: _PermitAuthority) -> None:
        self._callback = callback
        self._authority = authority

    def send(
        self,
        request: PhysicalTransportRequest,
        permit: TransportPermit,
        policy: TransportValidationPolicy,
    ) -> UntrustedTransportResponse:
        self._authority.consume(permit, request)
        try:
            raw_response = self._callback(request)
        except Exception as error:
            raise _TransportCallbackError("transport_callback_failed") from error
        response = UntrustedTransportResponse.model_validate(raw_response.model_dump(warnings=False))
        _validate_response(request, response, policy)
        return response


class ProductionTransportCoordinator:
    """Own permits and execute one already-admitted logical leader."""

    def __init__(
        self,
        repository: ProductionRequestRepository,
        callback: SyntheticTransport,
        permit_id_factory: Callable[[], str],
    ) -> None:
        """Bind durable state, one synthetic callback, and a private permit authority."""
        self._repository = repository
        self._authority = _PermitAuthority(permit_id_factory)
        self._transport = _ControlledTransport(callback, self._authority)

    def execute(
        self,
        request: PhysicalTransportRequest,
        attempt: ProductionPhysicalAttempt,
        reservation_id: str,
        keys: GateKeys,
        gate_policy: HierarchicalGatePolicy,
        transport_policy: TransportValidationPolicy,
        lease: RuntimeLease,
        started_at: datetime,
        completed_at: datetime,
    ) -> TransportExecutionResult:
        """Run gate, permit, send, validation, and durable result finalization once."""
        request = PhysicalTransportRequest.model_validate(request.model_dump(warnings=False))
        if (
            request.logical_request_id != attempt.logical_request_id
            or request.physical_attempt_id != attempt.physical_attempt_id
        ):
            raise RuntimeStorageError("transport_attempt_mismatch")
        reservation = self._repository.acquire_gates(reservation_id, attempt, keys, gate_policy, lease, started_at)
        self._repository.mark_physical_attempt_started(attempt.physical_attempt_id, lease, started_at)
        try:
            permit = self._authority.issue(reservation)
        except Exception:
            self._repository.release_gates(reservation, "failed", lease, completed_at)
            return self._complete_failure(request, lease, completed_at, "permit_issuance_failed")
        try:
            response = self._transport.send(request, permit, transport_policy)
        except _TransportCallbackError:
            self._repository.record_unknown_outcome(reservation, lease, completed_at)
            self._repository.complete_logical_request(
                ProductionLogicalResult(
                    logical_request_id=request.logical_request_id,
                    outcome=LogicalResultOutcome.UNKNOWN,
                    completed_at=completed_at.isoformat(),
                    error_code="transport_outcome_unknown",
                ),
                lease,
                completed_at,
            )
            return TransportExecutionResult("unknown", "transport_outcome_unknown")
        except Exception:
            self._repository.release_gates(reservation, "failed", lease, completed_at)
            return self._complete_failure(request, lease, completed_at, "invalid_transport_response")
        if response.status_code == 429 and response.retry_after_seconds is not None:
            self._repository.record_retry_after(reservation, response.retry_after_seconds, lease, completed_at)
            return self._complete_failure(request, lease, completed_at, "rate_limited")
        if not 200 <= response.status_code < 300:
            self._repository.release_gates(reservation, "failed", lease, completed_at)
            return self._complete_failure(request, lease, completed_at, "provider_error")
        self._repository.release_gates(reservation, "succeeded", lease, completed_at)
        self._repository.complete_logical_request(
            ProductionLogicalResult(
                logical_request_id=request.logical_request_id,
                outcome=LogicalResultOutcome.SUCCEEDED,
                completed_at=completed_at.isoformat(),
                error_code=None,
            ),
            lease,
            completed_at,
        )
        candidate = TemporaryRawCandidate(
            physical_attempt_id=request.physical_attempt_id,
            body=response.body,
            sha256=hashlib.sha256(response.body).hexdigest(),
            media_type=response.media_type,
            encoding=response.encoding,
        )
        return TransportExecutionResult("succeeded", "ok", candidate)

    def _complete_failure(
        self, request: PhysicalTransportRequest, lease: RuntimeLease, completed_at: datetime, reason: str
    ) -> TransportExecutionResult:
        self._repository.complete_logical_request(
            ProductionLogicalResult(
                logical_request_id=request.logical_request_id,
                outcome=LogicalResultOutcome.FAILED,
                completed_at=completed_at.isoformat(),
                error_code=reason,
            ),
            lease,
            completed_at,
        )
        return TransportExecutionResult("failed", reason)


def _validate_response(
    request: PhysicalTransportRequest,
    response: UntrustedTransportResponse,
    policy: TransportValidationPolicy,
) -> None:
    allowed_origins = {request.origin, *policy.allowed_redirect_origins}
    if len(response.body) > policy.max_response_bytes:
        raise RuntimeStorageError("response_too_large")
    if response.media_type.lower() not in {value.lower() for value in policy.allowed_media_types}:
        raise RuntimeStorageError("response_media_type_rejected")
    if response.encoding.lower() not in {value.lower() for value in policy.allowed_encodings}:
        raise RuntimeStorageError("response_encoding_rejected")
    if response.final_origin not in allowed_origins:
        raise RuntimeStorageError("response_origin_rejected")
    if response.redirected and response.final_origin == request.origin:
        raise RuntimeStorageError("response_redirect_inconsistent")
    if not response.redirected and response.final_origin != request.origin:
        raise RuntimeStorageError("response_origin_inconsistent")
