"""Run independent provider requests concurrently with deterministic state commits."""

from collections.abc import Callable, Generator, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress

from stock_research_llm_orchestrator.agents.research_provider import WebTurnResult
from stock_research_llm_orchestrator.research.workflow import ResearchRequest


RoleExecution = Generator[ResearchRequest, WebTurnResult]


def run_group(
    executions: Sequence[RoleExecution],
    invoke: Callable[[ResearchRequest], WebTurnResult],
    *,
    concurrent: bool,
) -> None:
    """Resolve a wave together, then advance generators in their stable role order.

    Only provider calls execute in worker threads. Request preparation, evidence
    acquisition, validation and artifact writes remain on the coordinator thread.
    Successful sibling calls finish and retain their journals even if one fails.
    """
    active: list[tuple[RoleExecution, ResearchRequest]] = []
    for execution in executions:
        with suppress(StopIteration):
            active.append((execution, next(execution)))
    try:
        while active:
            if concurrent and len(active) > 1:
                with ThreadPoolExecutor(max_workers=len(active), thread_name_prefix="research-role") as pool:
                    futures = [pool.submit(invoke, request) for _, request in active]
                    # The executor waits for all siblings before propagating an error.
                    results = [future.result() for future in futures]
            else:
                results = [invoke(request) for _, request in active]
            pending: list[tuple[RoleExecution, ResearchRequest]] = []
            for (execution, _), result in zip(active, results, strict=True):
                with suppress(StopIteration):
                    pending.append((execution, execution.send(result)))
            active = pending
    finally:
        for execution in executions:
            execution.close()
