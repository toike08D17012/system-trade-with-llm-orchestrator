"""Offline same-Tokyo-date conversion with retained immutable evidence."""

import json
from collections.abc import Mapping
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from hashlib import sha256
from pathlib import Path
from typing import Literal

from stock_research_llm_orchestrator.contracts.base import StrictContractModel
from stock_research_llm_orchestrator.preparation.fx_evidence import FxMetadata, read_bundle, validate_fx_evidence
from stock_research_llm_orchestrator.preparation.market_revalidation import _safe_path
from stock_research_llm_orchestrator.preparation.price_acceptance import PriceAcceptanceIndex, validate_price_acceptance
from stock_research_llm_orchestrator.preparation.storage import publish_preparation
from stock_research_llm_orchestrator.sources.boj.code_api import BojFxDailySeries
from stock_research_llm_orchestrator.sources.yfinance.normalization import NormalizedPrices


class PriceFxRow(StrictContractModel):
    """Original source decimals and all independent reasons preventing conversion."""

    on: str
    close_jpy: Decimal | None
    usd_jpy: Decimal | None
    close_usd: Decimal | None
    missing_reasons: tuple[str, ...]


class PriceFxIndex(StrictContractModel):
    """Reproducible fixed-period conversion without a detailed-analysis readiness claim."""

    version: Literal[1] = 1
    analysis_ready: Literal[False] = False
    calculation: Literal["unadjusted-close-jpy-divided-by-usdjpy-v1"] = "unadjusted-close-jpy-divided-by-usdjpy-v1"
    precision: Literal[28] = 28
    rounding: Literal["ROUND_HALF_EVEN"] = "ROUND_HALF_EVEN"
    timing_limitation: Literal["Stock session close and BOJ 17:00 JST FX are not simultaneous observations."] = (
        "Stock session close and BOJ 17:00 JST FX are not simultaneous observations."
    )
    status: Literal["complete_with_limitations", "incomplete"]
    period_start: str
    period_end: str
    converted_count: int
    rows: tuple[PriceFxRow, ...]
    excluded_fx_dates: dict[str, str]
    files: dict[str, str]


def _evaluate(files: Mapping[str, bytes]) -> PriceFxIndex:
    price_files = {name.removeprefix("price/"): body for name, body in files.items() if name.startswith("price/")}
    fx_files = {name.removeprefix("fx/"): body for name, body in files.items() if name.startswith("fx/")}
    if set(files) - {"index.json"} != {f"price/{name}" for name in price_files} | {f"fx/{name}" for name in fx_files}:
        raise ValueError("price_fx_inventory_mismatch")
    validate_price_acceptance(price_files)
    validate_fx_evidence(fx_files)
    acceptance = PriceAcceptanceIndex.model_validate_json(price_files["index.json"])
    prices = NormalizedPrices.model_validate_json(price_files["preparation/normalized.json"])
    metadata = FxMetadata.model_validate_json(fx_files["metadata.json"])
    fx = BojFxDailySeries.model_validate_json(fx_files["normalized.json"])
    if acceptance.status == "pending" or prices.currency != "JPY":
        raise ValueError("price_fx_requires_accepted_jpy_prices")
    if (acceptance.period_start, acceptance.period_end) != (metadata.period_start, metadata.period_end):
        raise ValueError("price_fx_period_mismatch")
    days = json.loads(price_files["calendar.json"])["dates"]
    price_map = {row.on: row for row in prices.rows}
    fx_map = {row.observed_on: row.value for row in fx.observations}
    rows = []
    for day in days:
        if not acceptance.period_start <= day <= acceptance.period_end:
            continue
        reasons = []
        price = price_map.get(day)
        close = price.close if price is not None else None
        value = fx_map.get(day)
        if close is None:
            reasons.append("price_close_missing")
        elif not close.is_finite() or close <= 0:
            reasons.append("price_close_invalid")
        if day not in fx_map:
            reasons.append("fx_date_missing")
        elif value is None:
            reasons.append("fx_null")
        elif not value.is_finite() or value <= 0:
            reasons.append("fx_invalid")
        converted = None
        if not reasons and close is not None and value is not None:
            with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
                converted = close / value
        rows.append(
            PriceFxRow(on=day, close_jpy=close, usd_jpy=value, close_usd=converted, missing_reasons=tuple(reasons))
        )
    target = {row.on for row in rows}
    excluded = {
        day: "outside_fixed_period"
        if not acceptance.period_start <= day <= acceptance.period_end
        else "not_xtks_session"
        for day in fx_map
        if day not in target
    }
    count = sum(row.close_usd is not None for row in rows)
    return PriceFxIndex(
        status="complete_with_limitations" if count == len(rows) else "incomplete",
        period_start=acceptance.period_start,
        period_end=acceptance.period_end,
        converted_count=count,
        rows=tuple(rows),
        excluded_fx_dates=excluded,
        files={name: sha256(files[name]).hexdigest() for name in sorted(files) if name != "index.json"},
    )


def validate_price_fx(files: Mapping[str, bytes]) -> None:
    """Revalidate both source bundles and recalculate every result from retained bytes."""
    if PriceFxIndex.model_validate_json(files["index.json"]) != _evaluate(files):
        raise ValueError("price_fx_index_mismatch")


def join_price_fx(price_directory: Path, fx_directory: Path, output_directory: Path) -> PriceFxIndex:
    """Publish a separate conversion bundle using no transport or external service."""
    price, fx, output = map(_safe_path, (price_directory, fx_directory, output_directory))
    if any(output == source or output in source.parents or source in output.parents for source in (price, fx)):
        raise ValueError("price_fx_output_overlap")
    if output.exists():
        raise FileExistsError("price_fx_destination_exists")
    files = {f"price/{name}": body for name, body in read_bundle(price).items()}
    files.update({f"fx/{name}": body for name, body in read_bundle(fx).items()})
    index = _evaluate(files)
    files["index.json"] = index.model_dump_json(indent=2).encode()
    publish_preparation(output.parent, output.name, files, validator=validate_price_fx)
    return index
