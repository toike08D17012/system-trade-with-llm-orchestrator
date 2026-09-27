"""Prepare or replay local financial/disclosure candidate evidence."""

import argparse
from pathlib import Path

from stock_research_llm_orchestrator.preparation.financial_disclosure import prepare_financial, validate_financial
from stock_research_llm_orchestrator.preparation.financial_run import prepare_financial_run, validate_financial_run
from stock_research_llm_orchestrator.preparation.fx_evidence import read_bundle


def main(argv: list[str] | None = None) -> int:
    """Return zero for saved pending results and one for invalid input or publication."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--input", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("--input", type=Path, required=True)
    for command in ("prepare-run", "validate-run"):
        run = commands.add_parser(command)
        run.add_argument("--financial", type=Path, required=True)
        run.add_argument("--mapping-review", type=Path, required=True)
        run.add_argument("--adoption", type=Path, required=True)
        run.add_argument("--price-fx", type=Path)
        run.add_argument("--comparative", type=Path)
        run.add_argument("--output" if command == "prepare-run" else "--input", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            result = prepare_financial(args.input, args.output)
            print(result.model_dump_json())
        elif args.command == "validate":
            validate_financial(read_bundle(args.input))
            print("financial_disclosure: valid; offline_replay; analysis_ready=false")
        elif args.command == "prepare-run":
            result_run = prepare_financial_run(
                args.financial, args.mapping_review, args.adoption, args.output, args.price_fx, args.comparative
            )
            print(result_run.model_dump_json())
        else:
            validate_financial_run(
                read_bundle(args.input),
                read_bundle(args.financial),
                read_bundle(args.mapping_review),
                read_bundle(args.adoption),
                read_bundle(args.price_fx) if args.price_fx is not None else None,
                read_bundle(args.comparative) if args.comparative is not None else None,
            )
            print("financial_run: valid; offline_replay; analysis_ready=false")
    except (ValueError, OSError, RuntimeError, KeyError) as error:
        print(f"financial_disclosure_failed: {type(error).__name__}; no network or retry")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
