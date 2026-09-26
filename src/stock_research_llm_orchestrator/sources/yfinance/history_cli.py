"""Acquire native daily prices and retain data-level evidence only."""

import argparse
import hashlib
import json
from pathlib import Path

from stock_research_llm_orchestrator.sources.yfinance.native_history import (
    HistoryAcquisitionError,
    NativeHistoryClient,
)


def main() -> None:
    """Run one diagnostic acquisition through the shared provider guard."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--period", default="1y")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(mode=0o700, parents=True, exist_ok=False)
    try:
        result = NativeHistoryClient().history(args.symbol, period=args.period)
    except HistoryAcquisitionError as exc:
        summary = {"status": "failed", "reason": str(exc), "retry_at": getattr(exc, "retry_at", None)}
        (args.output / "metadata.json").write_text(json.dumps(summary, indent=2) + "\n")
        print(json.dumps(summary))
        raise SystemExit(1) from None
    csv = result.frame.to_csv().encode("utf-8")
    metadata = {
        **result.metadata,
        "status": "succeeded",
        "rows": len(result.frame),
        "first_date": str(result.frame.index[0]),
        "last_date": str(result.frame.index[-1]),
        "timezone": str(result.frame.index.tz),
        "artifact": "prices.csv",
        "artifact_kind": "yfinance_returned_dataframe",
        "sha256": hashlib.sha256(csv).hexdigest(),
        "jpx_eligibility_verified": False,
    }
    (args.output / "prices.csv").write_bytes(csv)
    (args.output / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    main()
