"""Prepare or validate local draft financial mapping reviews without network access."""

import argparse
from pathlib import Path

from stock_research_llm_orchestrator.preparation.financial_mapping import prepare_mapping, validate_mapping
from stock_research_llm_orchestrator.preparation.fx_evidence import read_bundle


def main(argv: list[str] | None = None) -> int:
    """Report aggregate status only; never print financial or raw source values."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--source", type=Path, required=True)
    prepare.add_argument("--proposal", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("--source", type=Path, required=True)
    validate.add_argument("--input", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            prepare_mapping(args.source, args.proposal, args.output)
        else:
            validate_mapping(read_bundle(args.input), read_bundle(args.source))
        print("financial_mapping: valid; mapping_pending; analysis_ready=false; offline_replay")
    except (ValueError, OSError, RuntimeError, KeyError) as error:
        print(f"financial_mapping_failed: {type(error).__name__}; no network or retry")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
