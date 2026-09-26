"""Shared gate boundaries and attempt-once failure behavior."""

from datetime import date

import pytest

from stock_research_llm_orchestrator.configuration import ConfigurationArtifact
from stock_research_llm_orchestrator.requests import AttemptResult, SourceBinding, TransportResponse, bind_source

from .fakes import Clock, Transport, coordinator, request


def binding(artifacts: tuple[ConfigurationArtifact, ...]) -> SourceBinding:
    """Select the synthetic approved source."""
    result = bind_source(artifacts, "fixture", 1, 1, date(2026, 9, 20))
    assert isinstance(result, SourceBinding)
    return result


def test_shared_interval_and_exact_boundary(artifacts: tuple[ConfigurationArtifact, ...]) -> None:
    """Two caller roles and tasks share the same start interval."""
    clock, transport = Clock(), Transport()
    shared = coordinator(clock, transport)
    source = binding(artifacts)
    assert shared.attempt(request(), source).status == "succeeded"
    other = request(task_id="task-2", consumer_role="claude_worker")
    blocked = shared.attempt(other, source)
    assert blocked.status == "blocked"
    assert blocked.retry_after_seconds == 2.5
    clock.seconds = 2.5
    assert shared.attempt(other, source).status == "succeeded"
    clock.seconds = 5
    assert shared.attempt(request(), source).status == "succeeded"
    assert len(transport.calls) == 3


@pytest.mark.parametrize("retry", [None, 0.0, 10.0, -1.0, float("nan"), float("inf")])
def test_failure_consumes_rate_and_valid_retry_after(
    artifacts: tuple[ConfigurationArtifact, ...],
    retry: float | None,
) -> None:
    """Failures never retry and only finite nonnegative delays enter cooldown."""
    clock, transport = Clock(), Transport()
    transport.response = TransportResponse(succeeded=False, status_category="rate_limited", retry_after_seconds=retry)
    shared, source = coordinator(clock, transport), binding(artifacts)
    failed = shared.attempt(request(), source)
    assert failed.status == "failed"
    assert len(transport.calls) == 1
    assert shared.attempt(request(), source).status == "blocked"
    clock.seconds = 2.5
    result = shared.attempt(request(), source)
    assert result.status == ("blocked" if retry == 10 else "failed")
    if retry == 10:
        assert result.retry_after_seconds == 7.5
        clock.seconds = 10
        assert shared.attempt(request(), source).status == "failed"


def test_exception_releases_occupancy(artifacts: tuple[ConfigurationArtifact, ...]) -> None:
    """Exceptions preserve consumption but release the in-flight slot."""
    clock, transport = Clock(), Transport()
    transport.error = RuntimeError("secret-token private-payload")
    shared, source = coordinator(clock, transport), binding(artifacts)
    failed = shared.attempt(request(), source)
    assert failed.reason_code == "transport_exception"
    assert "secret-token" not in repr(failed)
    clock.seconds = 2.5
    transport.error = None
    assert shared.attempt(request(), source).status == "succeeded"


def test_reentrant_occupancy_and_response_time_cooldown(artifacts: tuple[ConfigurationArtifact, ...]) -> None:
    """Reservation precedes transport; cooldown starts when the response arrives."""
    clock, source = Clock(), binding(artifacts)
    nested: list[AttemptResult] = []

    class Reentrant(Transport):
        def send(self, resource_key: str) -> TransportResponse:
            """Reenter while occupied, then simulate a slow failed response."""
            nested.append(shared.attempt(request(task_id="task-2"), source))
            clock.seconds = 6
            return TransportResponse(succeeded=False, status_category="rate_limited", retry_after_seconds=10.0)

    shared = coordinator(clock, Reentrant())
    result = shared.attempt(request(), source)
    assert nested[0].status == "blocked"
    assert nested[0].retry_after_seconds is None
    assert result.events[-1].error is not None
    assert result.events[-1].error.cooldown_until == "2026-09-20T00:00:16+00:00"
    assert shared.attempt(request(), source).retry_after_seconds == 10


def test_window_conflict_and_independent_domains(artifacts: tuple[ConfigurationArtifact, ...]) -> None:
    """Window limits survive failures; incompatible domains cannot relax gates."""
    from dataclasses import replace

    from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.policies import SourceProfileV1

    def changed_source(**changes: object) -> SourceBinding:
        changed = tuple(
            replace(item, model=item.model.model_copy(update=changes))
            if isinstance(item.model, SourceProfileV1)
            else item
            for item in artifacts
        )
        return binding(changed)

    source = changed_source(
        rate_window_seconds=binding(artifacts).profile.rate_window_seconds.model_copy(update={"value": 10})
    )
    clock, transport = Clock(), Transport()
    shared = coordinator(clock, transport)
    transport.error = RuntimeError("private")
    assert shared.attempt(request(), source).status == "failed"
    clock.seconds = 2.5
    assert shared.attempt(request(), source).status == "failed"
    clock.seconds = 5
    blocked = shared.attempt(request(), source)
    assert blocked.status == "blocked" and blocked.retry_after_seconds == 5
    assert shared.attempt(request(), binding(artifacts)).reason_code == "conflicting_rate_domain"
    independent = changed_source(rate_domain="independent")
    assert shared.attempt(request(), independent).status == "failed"
    clock.seconds = 10
    transport.error = None
    assert shared.attempt(request(), source).status == "succeeded"


def test_longer_existing_cooldown_is_preserved(artifacts: tuple[ConfigurationArtifact, ...]) -> None:
    """Updating a deadline never shortens an already longer provider cooldown."""
    # With concurrency one this state cannot arise from another public attempt;
    # seed it while transport is in flight to exercise the defensive max rule.
    clock, source = Clock(), binding(artifacts)

    class ExistingCooldown(Transport):
        def send(self, resource_key: str) -> TransportResponse:
            """Simulate an existing longer deadline at response receipt."""
            shared._domains[source.profile.rate_domain].cooldown = 20
            return TransportResponse(succeeded=False, status_category="rate_limited", retry_after_seconds=5.0)

    shared = coordinator(clock, ExistingCooldown())
    assert shared.attempt(request(), source).status == "failed"
    assert shared.attempt(request(), source).retry_after_seconds == 20


def test_two_fixture_clients_share_cooldown(artifacts: tuple[ConfigurationArtifact, ...]) -> None:
    """Distinct task/role clients cannot bypass another client's cooldown."""
    from .fakes import FixtureClient

    clock, transport = Clock(), Transport()
    source = binding(artifacts)
    shared = coordinator(clock, transport)
    first = FixtureClient(shared, request())
    second = FixtureClient(shared, request(task_id="task-2", consumer_role="claude_worker"))
    transport.response = TransportResponse(succeeded=False, status_category="rate_limited", retry_after_seconds=10.0)
    assert first.fetch(source).status == "failed"
    assert second.fetch(source).retry_after_seconds == 10
    assert len(transport.calls) == 1
    clock.seconds = 10
    transport.response = TransportResponse(succeeded=True, payload=b"fixture")
    assert second.fetch(source).status == "succeeded"
    assert first.fetch(source).retry_after_seconds == 2.5
