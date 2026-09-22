from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from stock_research_llm_orchestrator.requests.production import (
    ProductionLogicalRequest,
    QueuePolicy,
    RuntimeLease,
    RuntimeLeasePolicy,
)
from stock_research_llm_orchestrator.requests.storage import (
    ProductionRequestRepository,
    RuntimeStorageError,
    initialize_runtime_storage,
)


NOW = datetime(2026, 9, 22, tzinfo=UTC)


def _runtime(tmp_path: Path) -> tuple[ProductionRequestRepository, RuntimeLease]:
    root = tmp_path / ".runtime"
    root.mkdir(mode=0o700)
    repository = initialize_runtime_storage(root)
    return repository, repository.acquire_lease("owner-a", NOW, RuntimeLeasePolicy())


def _request(request_id: str, task_id: str, offset: int) -> ProductionLogicalRequest:
    return ProductionLogicalRequest(
        logical_request_id=request_id,
        task_id=task_id,
        source_id="fixture",
        operation="history",
        request_fingerprint=f"{offset + 1:064x}",
        source_approval_version=1,
        source_profile_version=1,
        credential_scope_alias=None,
        egress_scope="default",
        created_at=(NOW + timedelta(milliseconds=offset)).isoformat(),
    )


def _enqueue(
    repository: ProductionRequestRepository,
    lease: RuntimeLease,
    request_id: str,
    task_id: str,
    offset: int,
    rate_domain: str = "provider-a",
    policy: QueuePolicy | None = None,
) -> None:
    repository.enqueue_logical_request(
        _request(request_id, task_id, offset),
        rate_domain,
        lease,
        NOW + timedelta(milliseconds=offset),
        policy or QueuePolicy(),
    )


def test_queue_policy_rejects_contradictory_hierarchy() -> None:
    """Require nested queue limits to be internally consistent."""
    with pytest.raises(ValidationError, match="must not exceed"):
        QueuePolicy(global_limit=2, rate_domain_limit=3, task_rate_domain_limit=1)


def test_scheduler_preserves_task_fifo_and_round_robin(tmp_path: Path) -> None:
    """Alternate active tasks while retaining FIFO within each task."""
    repository, lease = _runtime(tmp_path)
    _enqueue(repository, lease, "a-1", "task-a", 1)
    _enqueue(repository, lease, "a-2", "task-a", 2)
    _enqueue(repository, lease, "b-1", "task-b", 3)
    _enqueue(repository, lease, "b-2", "task-b", 4)

    claimed = [
        repository.claim_next_queued("provider-a", lease, NOW + timedelta(seconds=index)) for index in range(1, 5)
    ]

    assert [item.logical_request_id if item else None for item in claimed] == ["a-1", "b-1", "a-2", "b-2"]
    assert repository.claim_next_queued("provider-a", lease, NOW + timedelta(seconds=5)) is None


def test_provider_queues_progress_independently(tmp_path: Path) -> None:
    """Allow a provider to dequeue without consuming another provider's item."""
    repository, lease = _runtime(tmp_path)
    _enqueue(repository, lease, "a-1", "task-a", 1, "provider-a")
    _enqueue(repository, lease, "b-1", "task-b", 2, "provider-b")

    provider_b = repository.claim_next_queued("provider-b", lease, NOW + timedelta(seconds=1))
    provider_a = repository.claim_next_queued("provider-a", lease, NOW + timedelta(seconds=2))

    assert provider_b is not None and provider_b.logical_request_id == "b-1"
    assert provider_a is not None and provider_a.logical_request_id == "a-1"


@pytest.mark.parametrize(
    ("policy", "items"),
    [
        (
            QueuePolicy(global_limit=2, rate_domain_limit=2, task_rate_domain_limit=2),
            [("a", "t1", "p1"), ("b", "t2", "p2")],
        ),
        (
            QueuePolicy(global_limit=3, rate_domain_limit=2, task_rate_domain_limit=2),
            [("a", "t1", "p1"), ("b", "t2", "p1")],
        ),
        (QueuePolicy(global_limit=3, rate_domain_limit=3, task_rate_domain_limit=1), [("a", "t1", "p1")]),
    ],
)
def test_queue_bounds_reject_and_audit(tmp_path: Path, policy: QueuePolicy, items: list[tuple[str, str, str]]) -> None:
    """Persist a terminal rejection instead of growing any bounded scope."""
    repository, lease = _runtime(tmp_path)
    for offset, (request_id, task_id, provider) in enumerate(items, start=1):
        _enqueue(repository, lease, request_id, task_id, offset, provider, policy)

    with pytest.raises(RuntimeStorageError, match="queue_full"):
        _enqueue(repository, lease, "rejected", items[0][1], 20, items[0][2], policy)

    assert repository.logical_request_state("rejected") == "failed"
    assert repository.queue_events("rejected") == (
        ("requested", None),
        ("rejected", "queue_full"),
    )


def test_queue_lifecycle_events_are_durable(tmp_path: Path) -> None:
    """Persist requested, queued, and dequeued in lifecycle order."""
    repository, lease = _runtime(tmp_path)
    _enqueue(repository, lease, "logical-1", "task-a", 1)

    repository.claim_next_queued("provider-a", lease, NOW + timedelta(seconds=1))

    assert repository.queue_events("logical-1") == (
        ("requested", None),
        ("queued", None),
        ("dequeued", None),
    )
