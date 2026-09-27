"""Prepare or replay a local aggregate of two approved financial reports."""

import argparse
from pathlib import Path

from stock_research_llm_orchestrator.preparation.financial_multi_report import (
    DEPENDENCIES,
    prepare_multi_report,
    validate_multi_report,
)
from stock_research_llm_orchestrator.preparation.fx_evidence import read_bundle


def main(argv: list[str] | None = None) -> int:
    """Display aggregate validity without exposing source values."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("prepare", "validate"):
        sub = commands.add_parser(command)
        for name in DEPENDENCIES:
            sub.add_argument("--" + name.replace("_", "-"), type=Path, required=True)
        sub.add_argument("--pair", type=Path)
        sub.add_argument("--pair-adoption", type=Path)
        sub.add_argument("--interim", type=Path)
        sub.add_argument("--interim-source", type=Path)
        sub.add_argument("--output" if command == "prepare" else "--input", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        paths = {name: getattr(args, name) for name in DEPENDENCIES}
        for name in ("pair", "pair_adoption", "interim", "interim_source"):
            if getattr(args, name) is not None:
                paths[name] = getattr(args, name)
        if args.command == "prepare":
            prepare_multi_report(paths, args.output)
        else:
            validate_multi_report(read_bundle(args.input), {k: read_bundle(p) for k, p in paths.items()})
        print("financial_multi_report: valid; offline_replay; analysis_ready=false")
    except (ValueError, OSError, RuntimeError, KeyError) as error:
        print(f"financial_multi_report_failed: {type(error).__name__}; no network or retry")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
