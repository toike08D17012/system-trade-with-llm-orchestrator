"""Prepare or replay local financial/disclosure candidate evidence."""

import argparse
from pathlib import Path

from stock_research_llm_orchestrator.preparation.financial_disclosure import prepare_financial, validate_financial
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
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            result = prepare_financial(args.input, args.output)
            print(result.model_dump_json())
        else:
            validate_financial(read_bundle(args.input))
            print("financial_disclosure: valid; offline_replay; analysis_ready=false")
    except (ValueError, OSError, RuntimeError, KeyError) as error:
        print(f"financial_disclosure_failed: {type(error).__name__}; no network or retry")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
