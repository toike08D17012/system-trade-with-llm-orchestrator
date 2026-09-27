"""Comparative coverage distinguishes accepted periods from filing counts."""

from datetime import date
from hashlib import sha256
from pathlib import Path

import pytest

from stock_research_llm_orchestrator.preparation.financial_acceptance import FinancialValue
from stock_research_llm_orchestrator.preparation.financial_comparative import (
    APPROVED_COMPARATIVE_POLICY_SHA256,
    AnnualPeriod,
    ComparativePeriodValues,
    ComparativePolicy,
    annual_coverage,
    evaluate_comparative,
)
from stock_research_llm_orchestrator.preparation.financial_mapping import METRICS


PERIOD = AnnualPeriod(start_date=date(2024, 4, 1), end_date=date(2025, 3, 31))


def _record(accepted: int) -> ComparativePeriodValues:
    return ComparativePeriodValues(
        period=PERIOD,
        values=tuple(
            FinancialValue(
                metric=metric,
                status="accepted" if index < accepted else "unaccepted",
                value="0" if index < accepted else None,
                concept=None,
                start_date=None if metric in {"assets", "equity"} else PERIOD.start_date,
                end_date=PERIOD.end_date,
                references=(),
                reasons=() if index < accepted else ("candidate_nil",),
            )
            for index, metric in enumerate(METRICS)
        ),
    )


@pytest.mark.parametrize(("count", "status"), [(0, "missing"), (1, "partial"), (5, "partial"), (6, "complete")])
def test_count_complete_periods_only(count: int, status: str) -> None:
    """Partial values and duplicate references do not create complete periods."""
    next_period = AnnualPeriod(start_date=date(2025, 4, 1), end_date=date(2026, 3, 31))
    coverage = annual_coverage((PERIOD, next_period), (_record(count),))
    assert coverage[0].status == status and coverage[0].accepted_count == count
    assert len(coverage[0].missing_metrics) == 6 - count
    assert coverage[1].status == "missing"
    with pytest.raises(ValueError, match="coverage_period"):
        annual_coverage((PERIOD,), (_record(count), _record(count)))
    with pytest.raises(ValueError, match="coverage_period"):
        annual_coverage((next_period,), (_record(count),))


def test_metric_and_period_keys_cannot_be_duplicated_or_reassigned() -> None:
    """Reject missing metric keys and period reassignment."""
    record = _record(6)
    for values in (record.values[:-1], (*record.values[:-1], record.values[0])):
        with pytest.raises(ValueError, match="metric_keys"):
            ComparativePeriodValues(period=PERIOD, values=values)
    with pytest.raises(ValueError, match="value_period"):
        ComparativePeriodValues(
            period=AnnualPeriod(start_date=date(2023, 4, 1), end_date=date(2024, 3, 31)), values=record.values
        )


def test_only_pinned_comparative_policy_is_approved() -> None:
    """A modified policy cannot authorize additional comparative scope."""
    body = Path("config/financial-mapping/7203-2026-comparative-approved.json").read_bytes()
    assert sha256(body).hexdigest() == APPROVED_COMPARATIVE_POLICY_SHA256
    policy = ComparativePolicy.model_validate_json(body)
    assert len(policy.periods) == 2 and len(policy.required_annual_periods) == 5
    for altered in (body + b" ", body.replace(b'"approved"', b'"draft"'), b"{}"):
        with pytest.raises(ValueError, match="policy_not_approved"):
            evaluate_comparative({}, {}, {}, altered)
    data = policy.model_dump()
    data["periods"] = (policy.periods[0], policy.periods[0])
    with pytest.raises(ValueError, match="period_scope"):
        ComparativePolicy.model_validate(data)
