"""Contract-valid, secret-free event sequences."""

import pytest

from stock_research_llm_orchestrator.configuration import ConfigurationArtifact
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.external_requests import ExternalRequestEventV1
from stock_research_llm_orchestrator.requests import Coordinator, TransportResponse

from .fakes import Clock, Transport, coordinator, request
from .test_coordinator import binding


def test_event_sequence(artifacts: tuple[ConfigurationArtifact, ...]) -> None:
    """Success and blocked events match actual work and contain no payload."""
    clock, transport = Clock(), Transport()
    shared, source = coordinator(clock, transport), binding(artifacts)
    succeeded = shared.attempt(request(), source)
    assert [e.event_type for e in succeeded.events] == [
        "logical_requested",
        "gates_evaluated",
        "physical_started",
        "physical_succeeded",
    ]
    blocked = shared.attempt(request(), source)
    assert len(blocked.events) == 2
    assert all(e.physical_attempt_id is None for e in blocked.events)
    for event in (*succeeded.events, *blocked.events):
        raw = event.model_dump_json()
        assert ExternalRequestEventV1.model_validate_json(raw) == event
        assert "private-payload" not in raw and "private-fixture-key" not in raw
        assert event.usage == () and event.queue_wait_ms == 0
        assert event.cache_decision == event.single_flight_decision == "not_applicable"


@pytest.mark.parametrize("invalid", ["https://private.example/token", "", "bad value"])
def test_invalid_input_no_consumption(artifacts: tuple[ConfigurationArtifact, ...], invalid: str) -> None:
    """Reject bypassed model validation and invalid injected IDs before send."""
    clock, transport = Clock(), Transport()
    source = binding(artifacts)
    shared = coordinator(clock, transport)
    assert shared.attempt(request().model_copy(update={"origin": invalid}), source).status == "rejected"
    invalid_ids = Coordinator(transport, clock.monotonic, clock.utcnow, lambda: invalid, lambda: "physical")
    assert invalid_ids.attempt(request(), source).status == "rejected"
    assert not transport.calls
    assert shared.attempt(request(), source).status == "succeeded"


def test_success_with_retry_after_is_failure(artifacts: tuple[ConfigurationArtifact, ...]) -> None:
    """Do not adopt cooldown from an inconsistent success response."""
    clock, transport = Clock(), Transport()
    transport.response = TransportResponse(succeeded=True, payload=b"private-payload", retry_after_seconds=99.0)
    shared, source = coordinator(clock, transport), binding(artifacts)
    result = shared.attempt(request(), source)
    assert result.status == "failed"
    assert result.reason_code == "invalid_transport_response"
    assert len(result.events) == 4
    clock.seconds = 2.5
    assert shared.attempt(request(), source).status == "failed"


@pytest.mark.parametrize("now", [-1.0, float("nan"), float("inf")])
def test_invalid_clock(artifacts: tuple[ConfigurationArtifact, ...], now: float) -> None:
    """Invalid injected clocks reject before physical state consumption."""
    clock, transport = Clock(), Transport()
    source = binding(artifacts)
    shared = Coordinator(transport, lambda: now, clock.utcnow, lambda: "event", lambda: "physical")
    assert shared.attempt(request(), source).status == "rejected"
    assert not transport.calls


def test_invalid_physical_id_and_naive_clock(artifacts: tuple[ConfigurationArtifact, ...]) -> None:
    """Validate physical IDs and timezone before reserving a slot."""
    from itertools import count

    clock, transport = Clock(), Transport()
    source = binding(artifacts)
    ids = count()
    invalid_id = Coordinator(transport, clock.monotonic, clock.utcnow, lambda: f"event-{next(ids)}", lambda: "bad id")
    assert invalid_id.attempt(request(), source).status == "rejected"
    naive = Coordinator(
        transport,
        clock.monotonic,
        lambda: clock.utcnow().replace(tzinfo=None),
        lambda: f"event-{next(ids)}",
        lambda: "physical",
    )
    assert naive.attempt(request(), source).status == "rejected"
    assert not transport.calls


def test_malformed_response_and_failure_events(artifacts: tuple[ConfigurationArtifact, ...]) -> None:
    """Malformed delays cannot leak into errors; cooldown events link one attempt."""
    clock, transport = Clock(), Transport()
    source = binding(artifacts)
    transport.response = TransportResponse(succeeded=False, status_category="provider_error").model_copy(
        update={"retry_after_seconds": "secret-token"}
    )
    shared = coordinator(clock, transport)
    malformed = shared.attempt(request(), source)
    assert malformed.status == "failed"
    assert "secret-token" not in repr(malformed)
    clock.seconds = 2.5
    transport.response = TransportResponse(succeeded=False, status_category="rate_limited", retry_after_seconds=5.0)
    failed = shared.attempt(request(), source)
    assert [e.event_type for e in failed.events] == [
        "logical_requested",
        "gates_evaluated",
        "physical_started",
        "physical_failed",
        "cooldown_recorded",
    ]
    assert len({e.physical_attempt_id for e in failed.events[2:]}) == 1
    assert failed.events[3].error == failed.events[4].error
    for event in failed.events:
        assert ExternalRequestEventV1.model_validate_json(event.model_dump_json()) == event


def test_response_validation_emits_no_sensitive_warnings(
    artifacts: tuple[ConfigurationArtifact, ...],
    recwarn: pytest.WarningsRecorder,
) -> None:
    """Even deliberately malformed responses must not leak through serialization warnings."""
    clock, transport = Clock(), Transport()
    transport.response = TransportResponse(succeeded=True, payload=b"fixture").model_copy(
        update={"payload": {"credential": "private-token"}}
    )
    result = coordinator(clock, transport).attempt(request(), binding(artifacts))
    assert result.status == "failed"
    assert not recwarn
    assert "private-token" not in repr(result)


def test_response_clock_failure_releases_occupancy(artifacts: tuple[ConfigurationArtifact, ...]) -> None:
    """A broken receipt clock produces a safe failure without losing the slot."""
    clock, source = Clock(), binding(artifacts)

    class BrokenClock(Transport):
        def send(self, resource_key: str) -> TransportResponse:
            """Break the clock only during the first response."""
            if not self.calls:
                clock.seconds = float("nan")
            return super().send(resource_key)

    transport = BrokenClock()
    shared = coordinator(clock, transport)
    result = shared.attempt(request(), source)
    assert result.status == "failed"
    assert result.reason_code == "invalid_response_clock_or_cooldown"
    clock.seconds = 2.5
    assert shared.attempt(request(), source).status == "succeeded"
