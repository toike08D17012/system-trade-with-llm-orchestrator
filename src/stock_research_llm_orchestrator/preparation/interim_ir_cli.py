"""Prepare or replay fixed interim IR evidence without network access."""

import argparse
from pathlib import Path

from stock_research_llm_orchestrator.preparation.fx_evidence import read_bundle
from stock_research_llm_orchestrator.preparation.interim_ir import prepare_interim, validate_interim


def main(argv: list[str] | None = None) -> int:
    """Keep financial values out of command output and error messages."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("prepare", "validate"):
        sub = commands.add_parser(command)
        sub.add_argument("--source", type=Path, required=True)
        sub.add_argument("--output" if command == "prepare" else "--input", type=Path, required=True)
        if command == "prepare":
            sub.add_argument("--policy", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            prepare_interim(args.source, args.policy, args.output)
        else:
            validate_interim(read_bundle(args.input), read_bundle(args.source))
        print("interim_ir: valid; offline_replay; analysis_ready=false")
    except (ValueError, OSError, RuntimeError, KeyError) as error:
        print(f"interim_ir_failed: {type(error).__name__}; no network or retry")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
