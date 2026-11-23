"""Calculate conditional total returns from a retained price and explicit assumptions."""

import json
from collections.abc import Sequence
from decimal import Decimal, InvalidOperation

from pydantic import JsonValue

from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.objectives import ScenarioV1


def amount(value: str) -> Decimal:
    """Reject nonfinite or negative untrusted monetary assumptions."""
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise ValueError("scenario_amount_invalid") from None
    if not result.is_finite() or result < 0:
        raise ValueError("scenario_amount_invalid")
    return result


def observed_price(excerpts: Sequence[dict[str, JsonValue]]) -> dict[str, JsonValue] | None:
    """Select one quality-passed JPY price; conflicting snapshots remain unavailable."""
    candidates: list[dict[str, JsonValue]] = []
    for excerpt in excerpts:
        if excerpt["source_id"] != "yfinance":
            continue
        snapshot = json.loads(str(excerpt["text"]))
        row = snapshot["last_observed_row"]
        close = row["close"]
        if snapshot["quality_passed"] is not True or snapshot["currency"] != "JPY" or close is None:
            continue
        price = amount(str(close))
        if price <= 0:
            continue
        candidates.append(
            {
                "price": str(price),
                "currency": "JPY",
                "on": row["on"],
                "evidence_id": excerpt["evidence_id"],
                "per_share_basis": "same share basis as retained close; future splits adjusted to this basis",
            }
        )
    return candidates[0] if len(candidates) == 1 else None


def total_return(
    scenario: ScenarioV1, price: dict[str, JsonValue] | None, *, source_references_available: bool = False
) -> dict[str, JsonValue]:
    """Calculate cumulative returns without reinvestment, tax, fees or invented inputs."""
    for value in (scenario.terminal_price, scenario.cumulative_dividends):
        if value is not None:
            amount(value)
    if scenario.terminal_price is None or scenario.cumulative_dividends is None or price is None:
        return {"status": "not_calculable", "reason": "Verified entry price or scenario assumptions are missing."}
    if scenario.currency != price["currency"] or scenario.per_share_basis != price["per_share_basis"]:
        raise ValueError("scenario_price_basis_mismatch")
    if (not scenario.evidence_ids and not source_references_available) or scenario.missing_information:
        return {"status": "not_calculable", "reason": "Scenario evidence or required information is missing."}
    entry = amount(str(price["price"]))
    if entry <= 0:
        raise ValueError("scenario_entry_price_invalid")
    capital = (amount(scenario.terminal_price) - entry) / entry
    income = amount(scenario.cumulative_dividends) / entry
    return {
        "status": "conditional",
        "capital_return": str(capital),
        "income_return": str(income),
        "total_return": str(capital + income),
        "months": scenario.months,
        "entry_price": price,
        "basis": "pre-tax, before fees, no dividend reinvestment; cumulative, not annualized",
    }
