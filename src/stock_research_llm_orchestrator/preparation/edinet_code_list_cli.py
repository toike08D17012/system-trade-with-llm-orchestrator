"""Acquire or validate the approved public EDINET code-list receipt."""

import argparse
from pathlib import Path

from stock_research_llm_orchestrator.preparation.edinet_code_list import (
    acquire_code_list,
    validate_code_list_receipt,
)
from stock_research_llm_orchestrator.preparation.fx_evidence import read_bundle


def main(argv: list[str] | None = None) -> int:
    """Run one explicit anonymous GET, or offline receipt validation."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("acquire", "validate"):
        sub = commands.add_parser(name)
        sub.add_argument("--runtime", type=Path, required=True)
        sub.add_argument("--runs", type=Path, required=True)
        if name == "acquire":
            sub.add_argument("--config", type=Path, required=True)
            sub.add_argument("--destination", required=True)
            sub.add_argument("--task-id", required=True)
            sub.add_argument("--allow-network", action="store_true", required=True)
        else:
            sub.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "acquire":
            result = acquire_code_list(
                config=args.config,
                runtime=args.runtime,
                runs=args.runs,
                destination=args.destination,
                task_id=args.task_id,
            )
        else:
            result = validate_code_list_receipt(read_bundle(args.receipt), runtime=args.runtime, runs=args.runs)
        print(
            f"edinet_code_list: valid; as_of={result.observation.as_of}; sha256={result.raw_reference.content_sha256}"
        )
    except (ValueError, OSError, RuntimeError, KeyError) as error:
        print(f"edinet_code_list_failed: {type(error).__name__}: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
