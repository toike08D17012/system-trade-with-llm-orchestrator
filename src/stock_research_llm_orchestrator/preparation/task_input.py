"""Construct validated single-security tasks for offline preparation."""

from collections.abc import Callable
from datetime import datetime

from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.task import (
    AnalysisHorizon,
    DetailedAnalysisTaskV1,
    MarketIdentityV1,
    SecurityIdentifierV1,
    TaskOrigin,
    TaskSafetyConstraintsV1,
)


TaskIdFactory = Callable[[], str]
AcceptedAtFactory = Callable[[], datetime]


def create_human_selected_task(
    security_code: str,
    evaluation_policy_version: int,
    *,
    mic: str,
    task_id_factory: TaskIdFactory,
    accepted_at_factory: AcceptedAtFactory,
) -> DetailedAnalysisTaskV1:
    """Create one human-selected task within the approved initial market scope.

    Args:
        security_code: One security code. Leading zeroes and ASCII letters are preserved.
        evaluation_policy_version: Approved evaluation policy version.
        mic: Market identifier. The initial scope accepts only ``XTKS``.
        task_id_factory: Factory for the system-owned task identifier.
        accepted_at_factory: Factory for the system-owned timezone-aware acceptance time.

    Returns:
        An immutable validated detailed-analysis task.

    Raises:
        ValueError: If the factory returns an offset-free acceptance time.
        pydantic.ValidationError: If any value violates the public task contract.
    """
    accepted_at = accepted_at_factory()
    if accepted_at.utcoffset() is None:
        raise ValueError("accepted_at_factory must return a timezone-aware datetime")
    if mic != "XTKS":
        raise ValueError("the initial preparation scope supports only the XTKS market")

    return DetailedAnalysisTaskV1(
        schema_id="detailed-analysis.detailed-analysis-task",
        schema_version=1,
        task_id=task_id_factory(),
        parent_task_id=None,
        task_accepted_at=accepted_at.isoformat(),
        origin=TaskOrigin.HUMAN_SELECTED,
        security=SecurityIdentifierV1(security_code=security_code, mic="XTKS"),
        market=MarketIdentityV1(mic="XTKS", timezone="Asia/Tokyo", instrument_scope="domestic_cash_equity"),
        analysis_horizons=(AnalysisHorizon.MEDIUM_TERM, AnalysisHorizon.LONG_TERM),
        evaluation_policy_version=evaluation_policy_version,
        human_document_language_override=None,
        constraints=TaskSafetyConstraintsV1(
            external_publication_allowed=False,
            brokerage_connection_allowed=False,
            order_submission_allowed=False,
            automated_trading_allowed=False,
            automated_final_investment_decision_allowed=False,
        ),
    )
