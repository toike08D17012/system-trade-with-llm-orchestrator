"""Deterministic clocks, IDs, and in-memory fixture transport."""

from datetime import UTC, datetime, timedelta
from itertools import count

from stock_research_llm_orchestrator.requests import (
    AttemptResult,
    Coordinator,
    CoordinatorRequest,
    SourceBinding,
    TransportResponse,
)


class Clock:
    """Advance explicitly, including while a fixture is being sent."""

    def __init__(self) -> None:
        """Start at a reproducible instant."""
        self.seconds = 0.0

    def monotonic(self) -> float:
        """Return simulated elapsed seconds."""
        return self.seconds

    def utcnow(self) -> datetime:
        """Return the corresponding UTC instant."""
        return datetime(2026, 9, 20, tzinfo=UTC) + timedelta(seconds=self.seconds)


class Transport:
    """Record calls without opening files or network connections."""

    def __init__(self) -> None:
        """Default to one successful fixture response."""
        self.calls: list[str] = []
        self.response = TransportResponse(succeeded=True, payload=b"private-payload")
        self.error: Exception | None = None

    def send(self, resource_key: str) -> TransportResponse:
        """Return a fixed response or raise a configured error."""
        self.calls.append(resource_key)
        if self.error is not None:
            raise self.error
        return self.response


def coordinator(clock: Clock, transport: Transport) -> Coordinator:
    """Create an instance with distinct deterministic ID sequences."""
    events, physical = count(), count()
    return Coordinator(
        transport, clock.monotonic, clock.utcnow, lambda: f"event-{next(events)}", lambda: f"physical-{next(physical)}"
    )


def request(**changes: str) -> CoordinatorRequest:
    """Create a caller identity with optional task and role overrides."""
    return CoordinatorRequest.model_validate(
        {
            "task_id": "task-1",
            "consumer_role": "codex_worker",
            "operation": "fixture-read",
            "origin": "fixture-origin",
            "resource_key": "private-fixture-key",
            "logical_request_id": "logical-1",
            "applicable_period": "2026-09",
            "freshness_identity": "snapshot-1",
            **changes,
        }
    )


class FixtureClient:
    """Minimal client proving explicit coordinator sharing across consumers."""

    def __init__(self, shared: Coordinator, identity: CoordinatorRequest) -> None:
        """Keep the caller identity and the explicitly shared coordinator."""
        self.shared = shared
        self.identity = identity

    def fetch(self, source: SourceBinding) -> AttemptResult:
        """Delegate every attempt to the shared gates."""
        return self.shared.attempt(self.identity, source)
