"""Build or validate a private Toyota financial-results HTML catalog."""

import argparse
from datetime import date
from pathlib import Path

from stock_research_llm_orchestrator.preparation.toyota_ir_catalog import (
    publish_catalog_bundle,
    validate_saved_catalog,
)


def main(argv: list[str] | None = None) -> int:
    """Parse saved official HTML or replay an existing private catalog."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("--current", type=Path, required=True)
    build.add_argument("--archive", type=Path, required=True)
    build.add_argument("--runs", type=Path, required=True)
    build.add_argument("--destination", required=True)
    build.add_argument("--start", type=date.fromisoformat, required=True)
    build.add_argument("--end", type=date.fromisoformat, required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("--bundle", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            publish_catalog_bundle(
                args.runs,
                args.destination,
                args.current.read_bytes(),
                args.archive.read_bytes(),
                args.start,
                args.end,
            )
            bundle = args.runs / args.destination
        else:
            bundle = args.bundle
        count = validate_saved_catalog(bundle)
        print(f"toyota_ir_catalog: valid; entries={count}; analysis_ready=false")
    except (ValueError, OSError, RuntimeError, KeyError, TypeError) as error:
        print(f"toyota_ir_catalog_failed: {type(error).__name__}: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
