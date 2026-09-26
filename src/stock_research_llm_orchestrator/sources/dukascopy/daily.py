"""Offline Node decoding with independent Decimal and candle provenance checks."""

import json
import os
import shutil
import subprocess
from datetime import UTC, date, datetime, timedelta
from decimal import Context, Decimal, localcontext
from pathlib import Path
from typing import Any, Literal

from stock_research_llm_orchestrator.contracts.base import StrictContractModel
from stock_research_llm_orchestrator.sources.protocol import CredentialFreeSourceIntent, SourceParameter


ORIGIN = "jetta.dukascopy.com"
BASE = f"https://{ORIGIN}/v1/candles/day/USD-JPY/BID"
DAY = 86400000


def bridge(payload: dict[str, object]) -> dict[str, Any]:
    """Run only the fixed offline script with no inherited Node injection settings."""
    script = Path(__file__).resolve().parents[4] / "node/dukascopy/bridge.cjs"
    if not script.exists():
        script = Path("/opt/dukascopy/bridge.cjs")
    node = shutil.which("node")
    if node is None:
        raise ValueError("dukascopy_node_required")
    env = {"PATH": os.defpath}
    if Path("/opt/dukascopy/node_modules").exists():
        env["NODE_PATH"] = "/opt/dukascopy/node_modules"
    try:
        completed = subprocess.run(
            [node, str(script)],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            env=env,
            timeout=30,
            check=True,
        )
        result = json.loads(completed.stdout)
    except subprocess.SubprocessError, ValueError:
        raise ValueError("dukascopy_bridge_failed") from None
    if not isinstance(result, dict) or result.get("package_version") != "1.50.0" or result.get("bridge_version") != 1:
        raise ValueError("dukascopy_bridge_version_mismatch")
    return result


def year_url(year: int, now: datetime) -> str:
    """Canonical year bucket, using the active-year endpoint only when appropriate."""
    if not 2000 <= year <= now.year or now.tzinfo is None:
        raise ValueError("invalid_dukascopy_year")
    return BASE + (
        f"/{year}" if year < now.year else f"?from={int(datetime(year, 1, 1, tzinfo=UTC).timestamp()) * 1000}"
    )


def urls(start: date, end: date, now: datetime) -> tuple[str, ...]:
    """Cross-check the library URL generator against the fixed Python allowlist."""
    if start > end or end >= now.date():
        raise ValueError("fx_period_requires_completed_utc_days")
    expected = tuple(year_url(year, now) for year in range(start.year, end.year + 1))
    result = bridge(
        {
            "command": "urls",
            "start": start.isoformat(),
            "end": (end + timedelta(days=1)).isoformat(),
            "now": now.isoformat(),
        }
    )
    if result["result"] != list(expected):
        raise ValueError("dukascopy_url_mismatch")
    return expected


def intent_for_url(url: str, year: int, now: datetime) -> CredentialFreeSourceIntent:
    """Bind an exact year target before sending."""
    if url != year_url(year, now):
        raise ValueError("dukascopy_url_not_approved")
    return CredentialFreeSourceIntent(
        source_id="dukascopy",
        operation="fx-daily-bid",
        origin=ORIGIN,
        resource_key=f"usdjpy-bid-d1-{year}",
        parameters=(SourceParameter(name="url", value=url),),
    )


class DailyRow(StrictContractModel):
    """Actual source candle; never a library-generated carry-forward row."""

    on: str
    timestamp: int
    interval_end: str
    close: Decimal
    confirmed: bool


class DailySeries(StrictContractModel):
    """Fixed UTC interpretation with unknown final quote/publication timestamps."""

    source: Literal["dukascopy"] = "dukascopy"
    price_type: Literal["bid"] = "bid"
    timeframe: Literal["d1"] = "d1"
    date_basis: Literal["UTC"] = "UTC"
    package_version: Literal["1.50.0"] = "1.50.0"
    bridge_version: Literal[1] = 1
    publication_time: None = None
    rows: tuple[DailyRow, ...]


def parse_daily(body: bytes, url: str, year: int, received: datetime) -> DailySeries:
    """Reconstruct exact close decimals and compare every real candle with Node."""
    if received.tzinfo is None:
        raise ValueError("fx_received_timezone_required")
    decoded = bridge({"command": "decode", "url": url, "body": body.decode("utf-8")})["result"]
    raw = json.loads(body, parse_float=Decimal)
    timestamp = raw["timestamp"]
    rows = []
    with localcontext(Context(prec=28)):
        multiplier = Decimal(str(raw["multiplier"]))
        close_units = Decimal(str(raw["close"])) / multiplier
        if close_units != close_units.to_integral_value():
            raise ValueError("fx_base_precision_invalid")
        for i, delta in enumerate(raw["times"]):
            timestamp += delta * DAY
            value_delta = raw["closes"][i]
            if isinstance(value_delta, bool) or not isinstance(value_delta, int):
                raise ValueError("fx_close_delta_invalid")
            close_units += value_delta
            close = close_units * multiplier
            observed = datetime.fromtimestamp(timestamp / 1000, UTC)
            node_row = decoded[i]
            if (
                observed.year != year
                or node_row["timestamp"] != timestamp
                or Decimal(str(node_row["close"])) != close
                or not close.is_finite()
                or close <= 0
            ):
                raise ValueError("fx_daily_candle_invalid")
            interval_end = observed + timedelta(days=1)
            rows.append(
                DailyRow(
                    on=observed.date().isoformat(),
                    timestamp=timestamp,
                    interval_end=interval_end.isoformat(),
                    close=close,
                    confirmed=interval_end <= received,
                )
            )
    return DailySeries(rows=tuple(rows))
