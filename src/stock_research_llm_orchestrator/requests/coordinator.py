"""Synchronous, reentrant, memory-only coordinator for injected offline fakes.

No queue, retry, cache, persistence, network transport, or thread safety is provided.
"""

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol

from pydantic import TypeAdapter

from stock_research_llm_orchestrator.contracts.base import Timestamp
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.external_requests import (
    CacheDecision,
    ExternalRequestEventType,
    ExternalRequestEventV1,
    GateOutcome,
    GateScope,
    RateGateResultV1,
    RequestErrorV1,
    RequestObservability,
    RequestOutcome,
    SingleFlightDecision,
)
from stock_research_llm_orchestrator.requests.models import (
    IDENTIFIER_ADAPTER,
    AttemptResult,
    CoordinatorRequest,
    SourceBinding,
    TransportResponse,
)


class FakeTransport(Protocol):
    """Internal fixture boundary; production implementations are out of scope."""

    def send(self, resource_key: str) -> TransportResponse:
        """Look up one offline fixture."""
        ...


@dataclass
class _Domain:
    limits: tuple[int, float, int, float, int, str]
    occupied: bool = False
    last_start: float | None = None
    starts: list[float] = field(default_factory=list)
    cooldown: float = 0


class Coordinator:
    """Share provider gates across callers in a single synchronous instance."""

    def __init__(
        self,
        transport: FakeTransport,
        monotonic: Callable[[], float],
        utcnow: Callable[[], datetime],
        event_id_factory: Callable[[], str],
        physical_id_factory: Callable[[], str],
    ) -> None:
        """Inject offline transport, clocks, and separate audit ID factories."""
        self._transport = transport
        self._monotonic = monotonic
        self._utcnow = utcnow
        self._event_id = event_id_factory
        self._physical_id = physical_id_factory
        self._domains: dict[str, _Domain] = {}
        self._last_clock: float | None = None

    def _clock(self) -> tuple[float, datetime]:
        now = self._monotonic()
        utc = self._utcnow()
        if (
            isinstance(now, bool)
            or not math.isfinite(now)
            or now < 0
            or (self._last_clock is not None and now < self._last_clock)
            or not isinstance(utc, datetime)
            or utc.utcoffset() != timedelta(0)
        ):
            raise ValueError("invalid_clock")
        TypeAdapter(Timestamp).validate_python(utc.isoformat(), strict=True)
        return now, utc

    def attempt(self, request: CoordinatorRequest, binding: SourceBinding | AttemptResult) -> AttemptResult:
        """Send at most once, immediately returning any shared gate restriction."""
        try:
            if not isinstance(binding, SourceBinding):
                return AttemptResult("rejected", "invalid_source_binding")
            request = CoordinatorRequest.model_validate(request.model_dump(warnings=False))
            limits = binding.limits()
            now, utc = self._clock()
            domain = self._domains.get(binding.profile.rate_domain, _Domain(limits))
            if domain.limits != limits:
                return AttemptResult("rejected", "conflicting_rate_domain")
            active = [start for start in domain.starts if now - start < limits[3]]
            interval = max(0.0, (domain.last_start + limits[1] - now) if domain.last_start is not None else 0)
            window = max(0.0, active[-limits[2]] + limits[3] - now) if len(active) >= limits[2] else 0
            remaining = (max(0.0, domain.cooldown - now), float(domain.occupied), interval, window)
            gates = tuple(
                RateGateResultV1(
                    scope=GateScope.PROVIDER,
                    key_alias=binding.profile.rate_domain,
                    outcome=(GateOutcome.COOLDOWN if index == 0 else GateOutcome.BLOCKED)
                    if value
                    else GateOutcome.ACQUIRED,
                    waited_ms=0,
                    detail=name,
                )
                for index, (name, value) in enumerate(
                    zip(("cooldown", "occupancy", "minimum_interval", "rolling_window"), remaining, strict=True)
                )
            )
            blocked = any(remaining)
            # Reserve all possible event IDs before physical state consumption.
            ids = tuple(
                IDENTIFIER_ADAPTER.validate_python(self._event_id(), strict=True) for _ in range(2 if blocked else 5)
            )
            if len(set(ids)) != len(ids):
                raise ValueError("duplicate_event_id")
            physical = None if blocked else IDENTIFIER_ADAPTER.validate_python(self._physical_id(), strict=True)
            fingerprint = binding.fingerprint(request)

            def event(
                index: int,
                kind: ExternalRequestEventType,
                *,
                outcome: RequestOutcome = RequestOutcome.PENDING,
                error: RequestErrorV1 | None = None,
                timestamp: datetime = utc,
            ) -> ExternalRequestEventV1:
                return ExternalRequestEventV1(
                    schema_id="detailed-analysis.external-request-event",
                    schema_version=1,
                    event_id=ids[index],
                    occurred_at=timestamp.isoformat(),
                    event_type=kind,
                    task_id=request.task_id,
                    logical_request_id=request.logical_request_id,
                    physical_attempt_id=physical if index >= 2 else None,
                    observability=RequestObservability.PHYSICAL_REQUEST,
                    consumer_role=request.consumer_role,
                    provider=binding.profile.rate_domain,
                    operation=request.operation,
                    origin=request.origin,
                    credential_scope_alias=binding.profile.credential_scope_alias,
                    source_approval_reference=binding.approval_reference,
                    source_profile_reference=binding.profile_reference,
                    request_fingerprint=fingerprint,
                    cache_decision=CacheDecision.NOT_APPLICABLE,
                    cache_artifact_reference=None,
                    single_flight_decision=SingleFlightDecision.NOT_APPLICABLE,
                    leader_logical_request_id=None,
                    queue_wait_ms=0,
                    gate_results=() if index == 0 else gates,
                    outcome=outcome,
                    error=error,
                    usage=(),
                    automatic_retry_allowed=False,
                    direct_connection_fallback_allowed=False,
                )

            events = [
                event(0, ExternalRequestEventType.LOGICAL_REQUESTED),
                event(1, ExternalRequestEventType.GATES_EVALUATED),
            ]
            if blocked:
                return AttemptResult(
                    "blocked",
                    "rate_gate_blocked",
                    retry_after_seconds=None if domain.occupied else max(remaining),
                    events=tuple(events),
                )
            events.append(event(2, ExternalRequestEventType.PHYSICAL_STARTED))
        except Exception:
            # Caller-supplied clocks and factories may contain secrets in exceptions.
            return AttemptResult("rejected", "invalid_attempt_input")

        self._domains[binding.profile.rate_domain] = domain
        self._last_clock = now
        domain.occupied = True
        domain.last_start = now
        domain.starts = [*active, now]
        response: TransportResponse | None = None
        reason = "transport_exception"
        retry: float | None = None
        cooldown_until: str | None = None
        received_utc = utc
        try:
            try:
                raw_response = self._transport.send(request.resource_key)
                reason = "invalid_transport_response"
                response = TransportResponse.model_validate(raw_response.model_dump(warnings=False))
                if not response.is_valid():
                    response = None
                    reason = "invalid_transport_response"
            except Exception:
                response = None
            if response is not None:
                reason = response.status_category
            try:
                received, received_utc = self._clock()
                self._last_clock = received
                if response is not None and response.retry_after_seconds is not None:
                    retry = response.retry_after_seconds
                    deadline = max(domain.cooldown, received + retry)
                    if not math.isfinite(deadline):
                        raise ValueError("invalid_cooldown")
                    cooldown_until = (received_utc + timedelta(seconds=deadline - received)).isoformat()
                    domain.cooldown = deadline
            except Exception:
                response = None
                reason = "invalid_response_clock_or_cooldown"
                retry = None
                cooldown_until = None
                received_utc = utc
        finally:
            domain.occupied = False
        if response is not None and response.succeeded:
            events.append(
                event(
                    3,
                    ExternalRequestEventType.PHYSICAL_SUCCEEDED,
                    outcome=RequestOutcome.SUCCEEDED,
                    timestamp=received_utc,
                )
            )
            return AttemptResult("succeeded", "ok", payload=response.payload, events=tuple(events))
        error = RequestErrorV1(
            error_class=reason,
            message="Offline physical attempt failed.",
            retry_after_seconds=retry,
            cooldown_until=cooldown_until,
            response_status_code=None,
        )
        events.append(
            event(
                3,
                ExternalRequestEventType.PHYSICAL_FAILED,
                outcome=RequestOutcome.FAILED,
                error=error,
                timestamp=received_utc,
            )
        )
        if cooldown_until is not None:
            events.append(
                event(
                    4,
                    ExternalRequestEventType.COOLDOWN_RECORDED,
                    outcome=RequestOutcome.FAILED,
                    error=error,
                    timestamp=received_utc,
                )
            )
        return AttemptResult("failed", reason, retry_after_seconds=retry, events=tuple(events))
