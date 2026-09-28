"""Private annual calculation subset from already replayed financial records."""

import json
from collections.abc import Mapping
from datetime import timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal, InvalidOperation, localcontext
from hashlib import sha256

from stock_research_llm_orchestrator.preparation.financial_comparative import AnnualPeriod, ComparativePeriodValues
from stock_research_llm_orchestrator.preparation.financial_mapping import Metric


RATIOS: tuple[tuple[str, Metric], ...] = (
    ("operating_profit_to_revenue", "operating_profit"),
    ("parent_profit_to_revenue", "parent_profit"),
    ("operating_cash_flow_to_revenue", "operating_cash_flow"),
)


def calculate_annual(
    required: tuple[AnnualPeriod, ...],
    reports: Mapping[str, tuple[ComparativePeriodValues, ...]],
    value_hashes: Mapping[str, str],
) -> tuple[bytes, dict[str, object]]:
    """Calculate four annual slots per period; retain all equal input references.

    Inputs must have passed source adoption replay. Selectors and file hashes are
    internal provenance, not P1 evidence IDs. No source value is inferred.
    """
    if len(set(required)) != len(required) or set(reports) != set(value_hashes):
        raise ValueError("annual_calculation_input_scope_invalid")
    accepted: dict[tuple[AnnualPeriod, Metric], tuple[Decimal, list[dict[str, object]]]] = {}
    for dependency, records in sorted(reports.items()):
        if len({r.period for r in records}) != len(records):
            raise ValueError("annual_calculation_duplicate_period")
        for record in records:
            if record.period not in required:
                raise ValueError("annual_calculation_period_outside_window")
            for fact in record.values:
                if fact.status != "accepted":
                    continue
                try:
                    number = Decimal(fact.value) if fact.value is not None else Decimal("NaN")
                except InvalidOperation as error:
                    raise ValueError("annual_calculation_invalid_number") from error
                if not number.is_finite():
                    raise ValueError("annual_calculation_nonfinite_value")
                key = (record.period, fact.metric)
                if key in accepted and accepted[key][0] != number:
                    raise ValueError("annual_calculation_conflicting_value")
                if key not in accepted:
                    accepted[key] = (number, [])
                accepted[key][1].append(
                    {
                        "dependency": dependency,
                        "values_sha256": value_hashes[dependency],
                        "period": record.period.model_dump(mode="json"),
                        "metric": fact.metric,
                        "references": [r.model_dump(mode="json") for r in fact.references],
                    }
                )
    rows = []
    ordered = sorted(required, key=lambda p: p.start_date)
    for index, period in enumerate(ordered):
        previous = ordered[index - 1] if index else None
        for name, numerator_metric in (*RATIOS, ("revenue_year_over_year", "revenue")):
            prior = name == "revenue_year_over_year"
            numerator = accepted.get((period, numerator_metric))
            denominator_period = previous if prior else period
            denominator = accepted.get((denominator_period, "revenue")) if denominator_period is not None else None
            reason = None
            if prior and previous is None:
                reason = "prior_period_outside_window"
            elif (
                prior
                and previous is not None
                and not (
                    previous.end_date + timedelta(days=1) == period.start_date
                    and (
                        period.start_date.year - previous.start_date.year,
                        period.start_date.month,
                        period.start_date.day,
                    )
                    == (1, previous.start_date.month, previous.start_date.day)
                    and (period.end_date.year - previous.end_date.year, period.end_date.month, period.end_date.day)
                    == (1, previous.end_date.month, previous.end_date.day)
                )
            ):
                reason = "annual_periods_not_comparable"
            elif numerator is None or denominator is None:
                reason = "required_input_not_accepted"
            elif denominator[0] <= 0:
                reason = "denominator_not_positive"
            value = None
            if reason is None:
                assert numerator is not None and denominator is not None
                try:
                    with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
                        ratio = numerator[0] / denominator[0]
                        percent = (ratio - 1 if prior else ratio) * 100
                        rounded = percent.quantize(Decimal("0.000001"), rounding=ROUND_HALF_EVEN)
                        value = format(rounded if rounded else Decimal("0.000000"), "f")
                except InvalidOperation as error:
                    raise ValueError("annual_calculation_numeric_range_invalid") from error
            rows.append(
                {
                    "metric": name,
                    "period": period.model_dump(mode="json"),
                    "prior_period": previous.model_dump(mode="json") if prior and previous else None,
                    "value": value,
                    "missing_reason": reason,
                    "unit": "percent",
                    "inputs": [
                        reference for item in (numerator, denominator) if item is not None for reference in item[1]
                    ],
                }
            )
    body = json.dumps(
        {
            "version": 1,
            "kind": "internal-annual-calculation-subset",
            "calculation_logic_version": 1,
            "precision": 28,
            "rounding_method": "ROUND_HALF_EVEN",
            "decimal_places": 6,
            "analysis_ready": False,
            "rows": rows,
        },
        sort_keys=True,
        indent=2,
    ).encode()
    metadata = {
        "calculation_logic_version": 1,
        "rounding_method": "ROUND_HALF_EVEN",
        "precision": 28,
        "decimal_places": 6,
        "unit": "percent",
        "sha256": sha256(body).hexdigest(),
        "slot_count": len(rows),
        "calculated_count": sum(r["value"] is not None for r in rows),
        "missing_count": sum(r["value"] is None for r in rows),
        "limitations": ["annual_subset_only", "not_p1_calculated_evidence", "no_valuation_or_one_time_adjustment"],
    }
    return body, metadata
