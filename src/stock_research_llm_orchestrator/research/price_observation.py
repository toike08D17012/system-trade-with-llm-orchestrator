"""Select an attributed standard-route observation without promoting series freshness."""

import json
from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from pydantic import JsonValue

from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.acquisition_analysis import AcquiredEvidence
from stock_research_llm_orchestrator.sources.yfinance.normalization import NormalizedPrices


def standard_price_observation(evidence: Iterable[AcquiredEvidence], evaluation_at: str) -> dict[str, JsonValue] | None:
    """Return a machine-parsed conditional price, retaining unresolved calendar/freshness checks.

    The caller must validate acquired originals first. This does not certify the latest
    trading session, historical coverage, or current listing eligibility.
    """
    moment = datetime.fromisoformat(evaluation_at.replace("Z", "+00:00"))
    today = moment.astimezone(ZoneInfo("Asia/Tokyo")).date()
    candidates: list[dict[str, JsonValue]] = []
    for item in evidence:
        if item.source_id != "yfinance" or item.validation == "rejected":
            continue
        payload = json.loads(item.text)
        normalized = NormalizedPrices.model_validate_json(json.dumps(payload["normalized_prices"]))
        if (
            not payload["provider_identity_verified"]
            or normalized.currency != "JPY"
            or not normalized.rows
            or datetime.fromisoformat(item.retrieved_at.replace("Z", "+00:00")) > moment
        ):
            continue
        row = normalized.rows[-1]
        if row.close is None or not row.close.is_finite() or row.close <= Decimal(0) or row.on >= today.isoformat():
            continue
        candidates.append(
            {
                "price": str(row.close),
                "currency": "JPY",
                "on": row.on,
                "per_share_basis": "current listed-share basis",
                "source_id": item.evidence_id,
                "source_reference": item.source_reference,
                "retrieved_at": item.retrieved_at,
                "provenance": "machine_parsed_native_yfinance_observation",
                "validation": "pending",
                "limitations": [
                    "Original table and source identity checked; not an exchange-primary quote.",
                    "Trading-session completeness and current JPX snapshot freshness remain unconfirmed.",
                    "Conditional calculation reference, not a certified latest executable price.",
                ],
            }
        )
    if not candidates:
        return None
    return max(candidates, key=lambda value: (str(value["on"]), str(value["retrieved_at"])))
