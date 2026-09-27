"""Prepare and replay local-only financial adoption with aggregate output."""

import argparse
from pathlib import Path

from stock_research_llm_orchestrator.preparation.financial_acceptance import (
    FinancialAcceptanceManifest,
    prepare_acceptance,
    validate_acceptance,
)
from stock_research_llm_orchestrator.preparation.fx_evidence import read_bundle


def main(argv: list[str] | None = None) -> int:
    """Never print normalized values, provider text or exception details."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("prepare", "validate"):
        sub = commands.add_parser(command)
        sub.add_argument("--source", type=Path, required=True)
        sub.add_argument("--review", type=Path, required=True)
        if command == "prepare":
            sub.add_argument("--policy", type=Path, required=True)
            sub.add_argument("--output", type=Path, required=True)
        else:
            sub.add_argument("--input", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            result = prepare_acceptance(args.source, args.review, args.policy, args.output)
        else:
            files = read_bundle(args.input)
            validate_acceptance(files, read_bundle(args.source), read_bundle(args.review))
            result = FinancialAcceptanceManifest.model_validate_json(files["manifest.json"])
        print(
            f"financial_acceptance: valid; accepted_count={result.accepted_count}; analysis_ready=false; offline_replay"
        )
    except (ValueError, OSError, RuntimeError, KeyError) as error:
        print(f"financial_acceptance_failed: {type(error).__name__}; no network or retry")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
