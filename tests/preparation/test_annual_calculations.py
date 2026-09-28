"""Financial formulas and missingness use accepted annual inputs only."""

import json
from datetime import date
from decimal import ROUND_DOWN, localcontext
from hashlib import sha256

import pytest

from stock_research_llm_orchestrator.preparation.annual_calculations import calculate_annual
from stock_research_llm_orchestrator.preparation.financial_acceptance import FinancialValue
from stock_research_llm_orchestrator.preparation.financial_comparative import AnnualPeriod, ComparativePeriodValues
from stock_research_llm_orchestrator.preparation.financial_mapping import METRICS


def record(year: int, revenue: str = "300", profit: str = "100", cash: str = "-30") -> ComparativePeriodValues:
    """Construct scoped accepted values with explicit annual duration dates."""
    period = AnnualPeriod(start_date=date(year, 4, 1), end_date=date(year + 1, 3, 31))
    numbers = {"revenue": revenue, "operating_profit": profit, "parent_profit": "60", "operating_cash_flow": cash}
    return ComparativePeriodValues(
        period=period,
        values=tuple(
            FinancialValue(
                metric=m,
                status="accepted",
                value=numbers.get(m, "900"),
                concept=None,
                start_date=None if m in {"assets", "equity"} else period.start_date,
                end_date=period.end_date,
                references=(),
                reasons=(),
            )
            for m in METRICS
        ),
    )


def evaluate(records: tuple[ComparativePeriodValues, ...]) -> tuple[list[dict[str, object]], dict[str, object]]:
    """Read private rows while production CLI reports metadata only."""
    body, metadata = calculate_annual(
        tuple(r.period for r in records), {"comparative": records}, {"comparative": "1" * 64}
    )
    assert metadata["sha256"] == sha256(body).hexdigest()
    return json.loads(body)["rows"], metadata


def test_formula_results_rounding_and_negative_cash() -> None:
    """Known fractions and independent YoY expectations verify numerical meaning."""
    rows, metadata = evaluate((record(2024, revenue="200"), record(2025)))
    assert [r["value"] for r in rows[4:]] == ["33.333333", "20.000000", "-10.000000", "50.000000"]
    assert rows[3]["missing_reason"] == "prior_period_outside_window"
    assert metadata["calculated_count"] == 7 and metadata["missing_count"] == 1
    inputs = rows[4]["inputs"]
    assert isinstance(inputs, list)
    assert inputs[0]["dependency"] == "comparative"


@pytest.mark.parametrize(
    "profit,expected", [("0.000000025", "0.000002"), ("0.000000035", "0.000004"), ("-1", "-100.000000")]
)
def test_half_even_rounding_is_independent_of_ambient_context(profit: str, expected: str) -> None:
    """The explicit percent quantize handles ties and preserves losses."""
    with localcontext() as context:
        context.prec = 2
        context.rounding = ROUND_DOWN
        rows, metadata = evaluate((record(2025, revenue="1", profit=profit),))
    assert rows[0]["value"] == expected


@pytest.mark.parametrize("revenue", ["0", "-100"])
def test_nonpositive_denominators_are_missing(revenue: str) -> None:
    """A missing ratio is never a zero-valued substitute."""
    rows, metadata = evaluate((record(2024, revenue=revenue), record(2025)))
    assert all(r["value"] is None and r["missing_reason"] == "denominator_not_positive" for r in rows[:3])
    assert rows[-1]["missing_reason"] == "denominator_not_positive"


@pytest.mark.parametrize("revenue,expected", [("0", "-100.000000"), ("-10", "-105.000000")])
def test_yoy_current_revenue_zero_or_negative_is_not_a_denominator(revenue: str, expected: str) -> None:
    """Positive prior revenue supports the stated formula even for current losses."""
    rows, metadata = evaluate((record(2024, revenue="200"), record(2025, revenue=revenue)))
    assert rows[-1]["value"] == expected


def test_unaccepted_inputs_and_nonconsecutive_periods_remain_missing() -> None:
    """A stored number in an unaccepted fact cannot satisfy a required input."""
    old, current = record(2023), record(2025)
    values = tuple(
        v.model_copy(update={"status": "unaccepted"}) if v.metric == "operating_profit" else v for v in current.values
    )
    current = current.model_copy(update={"values": values})
    rows, metadata = evaluate((old, current))
    assert rows[4]["missing_reason"] == "required_input_not_accepted"
    assert rows[-1]["missing_reason"] == "annual_periods_not_comparable"


def test_equal_duplicates_keep_every_source_reference() -> None:
    """Duplicated equal facts calculate once and retain both file selectors."""
    r = record(2025)
    body, metadata = calculate_annual(
        (r.period,), {"first": (r,), "second": (r,)}, {"first": "1" * 64, "second": "2" * 64}
    )
    rows = json.loads(body)["rows"]
    assert metadata["slot_count"] == 4
    assert [i["dependency"] for i in rows[0]["inputs"]] == ["first", "second", "first", "second"]


@pytest.mark.parametrize("revenue", ["301", "NaN", "Infinity", "invalid"])
def test_conflicts_and_invalid_numbers_stop(revenue: str) -> None:
    """Source disagreements do not select the first or last observation."""
    r = record(2025)
    with pytest.raises(ValueError, match="annual_calculation_"):
        calculate_annual(
            (r.period,), {"a": (r,), "b": (record(2025, revenue=revenue),)}, {"a": "1" * 64, "b": "2" * 64}
        )


def test_matching_year_labels_without_adjacent_dates_do_not_support_yoy() -> None:
    """An identical shortened window in each year is not consecutive annual data."""
    records = []
    for year in (2024, 2025):
        original = record(year)
        period = AnnualPeriod(start_date=date(year, 4, 1), end_date=date(year, 12, 31))
        values = tuple(v.model_copy(update={"end_date": period.end_date}) for v in original.values)
        records.append(ComparativePeriodValues(period=period, values=values))
    rows, metadata = evaluate(tuple(records))
    assert rows[-1]["value"] is None and rows[-1]["missing_reason"] == "annual_periods_not_comparable"


@pytest.mark.parametrize("mode", ["duplicate", "outside-window", "hash-scope"])
def test_input_scope_rejected(mode: str) -> None:
    """Every source period and file hash must be within the requested cohort."""
    r = record(2025)
    with pytest.raises(ValueError, match="annual_calculation_"):
        calculate_annual(
            () if mode == "outside-window" else (r.period,),
            {"a": (r, r) if mode == "duplicate" else (r,)},
            {} if mode == "hash-scope" else {"a": "1" * 64},
        )
