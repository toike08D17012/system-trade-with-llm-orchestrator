"""Explicit annual Dukascopy acquisition and independently usable offline conversion."""

import argparse
from datetime import date
from pathlib import Path

from stock_research_llm_orchestrator.preparation.dukascopy_fx import acquire_dukascopy_fx
from stock_research_llm_orchestrator.preparation.fx_evidence import revalidate_fx_diagnostic
from stock_research_llm_orchestrator.preparation.price_fx import join_price_fx


def main(argv: list[str] | None = None) -> int:
    """Run only the selected acquisition or offline join mode."""
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
    revalidate = commands.add_parser("revalidate")
    revalidate.add_argument("--config", type=Path, default=Path("config"))
    revalidate.add_argument("--runtime", type=Path, required=True)
    revalidate.add_argument("--runs", type=Path, required=True)
    revalidate.add_argument("--diagnostic", type=Path, required=True)
    revalidate.add_argument("--destination", required=True)
    revalidate.add_argument("--task-id", required=True)
    revalidate.add_argument("--logical-request-id", required=True)
    revalidate.add_argument("--start", type=date.fromisoformat, required=True)
    revalidate.add_argument("--end", type=date.fromisoformat, required=True)
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
        elif args.command == "revalidate":
            result = revalidate_fx_diagnostic(
                config=args.config,
                runtime=args.runtime,
                runs=args.runs,
                diagnostic=args.diagnostic,
                destination=args.destination,
                task_id=args.task_id,
                logical_request_id=args.logical_request_id,
                start=args.start,
                end=args.end,
            )
            print(f"fx_evidence: {result.status}; offline_revalidation; original_logical_result=failed")
        else:
            converted = join_price_fx(args.prices, args.fx, args.output)
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
