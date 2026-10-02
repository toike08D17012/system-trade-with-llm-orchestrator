"""Acquire Dukascopy FX evidence and join it with accepted prices."""

import argparse
from datetime import date
from pathlib import Path

from stock_research_llm_orchestrator.preparation.dukascopy_fx import (
    acquire_dukascopy_fx,
    validate_dukascopy_year_receipt,
)
from stock_research_llm_orchestrator.preparation.price_fx_v2 import join_price_fx_v2
from stock_research_llm_orchestrator.preparation.storage import read_bundle


def main(argv: list[str] | None = None) -> int:
    """Run the selected Dukascopy acquisition or offline validation mode."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    acquire = commands.add_parser("acquire")
    acquire.add_argument("--allow-network", action="store_true", required=True)
    acquire.add_argument("--config", type=Path, default=Path("config"))
    acquire.add_argument("--runtime", type=Path, required=True)
    acquire.add_argument("--runs", type=Path, required=True)
    acquire.add_argument("--destination", required=True)
    acquire.add_argument("--task-id", required=True)
    acquire.add_argument("--start", type=date.fromisoformat, required=True)
    acquire.add_argument("--end", type=date.fromisoformat, required=True)
    receipt = commands.add_parser("validate-year-receipt")
    receipt.add_argument("--input", type=Path, required=True)
    receipt.add_argument("--runtime", type=Path, required=True)
    receipt.add_argument("--runs", type=Path, required=True)
    join = commands.add_parser("join")
    join.add_argument("--prices", type=Path, required=True)
    join.add_argument("--fx", type=Path, required=True)
    join.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "acquire":
            acquired = acquire_dukascopy_fx(
                config=args.config,
                runtime=args.runtime,
                runs=args.runs,
                destination=args.destination,
                task_id=args.task_id,
                start=args.start,
                end=args.end,
            )
            print(f"fx_evidence: {acquired.status}; analysis_ready=false")
        elif args.command == "validate-year-receipt":
            validate_dukascopy_year_receipt(read_bundle(args.input), runtime=args.runtime, runs=args.runs)
            print("fx_year_receipt: valid; offline_replay; analysis_ready=false")
        else:
            converted = join_price_fx_v2(args.prices, args.fx, args.output)
            print(
                f"price_fx: {converted.status}; converted={converted.converted_count}/{len(converted.rows)}; "
                "analysis_ready=false"
            )
    except (ValueError, OSError, RuntimeError) as error:
        print(f"fx_evidence_failed: {type(error).__name__}; no automatic retry")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
