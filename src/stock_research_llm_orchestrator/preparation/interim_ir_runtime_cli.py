"""Acquire fixed IR PDFs or verify existing runtime provenance offline."""

import argparse
from pathlib import Path

from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.task import DetailedAnalysisTaskV1
from stock_research_llm_orchestrator.preparation.interim_ir_runtime import acquire_runtime, verify_runtime
from stock_research_llm_orchestrator.preparation.market_revalidation import _safe_path
from stock_research_llm_orchestrator.preparation.storage import read_bundle
from stock_research_llm_orchestrator.requests.storage import DATABASE_FILENAME, ProductionRequestRepository


def main(argv: list[str] | None = None) -> int:
    """Require explicit network opt-in; show no source values or response bodies."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("acquire", "validate"):
        sub = commands.add_parser(command)
        sub.add_argument("--runtime", type=Path, required=True)
        sub.add_argument("--raw-root", type=Path, required=True)
        sub.add_argument("--task", type=Path, required=True)
        sub.add_argument("--output" if command == "acquire" else "--input", type=Path, required=True)
        if command == "acquire":
            sub.add_argument("--config", type=Path, default=Path("config"))
            sub.add_argument("--policy", type=Path, required=True)
            sub.add_argument("--allow-network", action="store_true")
            sub.add_argument("--latest-disclosures", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "acquire" and not args.allow_network:
        parser.error("acquire requires --allow-network")
    try:
        task_bytes = _safe_path(args.task).read_bytes()
        if args.command == "acquire":
            result = acquire_runtime(
                config=args.config,
                runtime=args.runtime,
                raw_root=args.raw_root,
                output=args.output,
                task_bytes=task_bytes,
                policy_bytes=_safe_path(args.policy).read_bytes(),
                latest_disclosures=args.latest_disclosures,
            )
        else:
            database = _safe_path(args.runtime / DATABASE_FILENAME)
            if not database.is_file():
                raise ValueError("ir_runtime_database_required")
            result = verify_runtime(
                read_bundle(args.input),
                ProductionRequestRepository(database),
                args.raw_root,
                DetailedAnalysisTaskV1.model_validate_json(task_bytes),
                args.runtime,
            )
        print(f"interim_ir_runtime: valid; receipts={len(result.receipts)}; analysis_ready=false")
    except (ValueError, OSError, RuntimeError, KeyError) as error:
        print(f"interim_ir_runtime_failed: {type(error).__name__}; no retry")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
