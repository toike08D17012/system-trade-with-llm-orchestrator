"""Plan, prepare and validate a price/FX run slice without network access."""

import argparse
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.task import DetailedAnalysisTaskV1
from stock_research_llm_orchestrator.preparation.fx_evidence import read_bundle
from stock_research_llm_orchestrator.preparation.market_revalidation import _read_file, _safe_path, _timestamp
from stock_research_llm_orchestrator.preparation.price_fx_run import (
    prepare_price_fx_run,
    publish_run_preparation,
    validate_price_fx_run,
)
from stock_research_llm_orchestrator.preparation.run_requirements import calendar_inputs, resolve_requirements


def validate_run_plan(files: Mapping[str, bytes]) -> None:
    """Reproduce a saved task-bound plan at its recorded evaluation time."""
    expected_names = {
        "task.json",
        "requirements.json",
        "index.json",
        "market-profile.yaml",
        "calendar.json",
        "calendar-metadata.json",
        "research-note.md",
    }
    if set(files) != expected_names:
        raise ValueError("run_plan_inventory_mismatch")
    saved = json.loads(files["requirements.json"])
    req = resolve_requirements(files, saved["checked_at"])
    task = DetailedAnalysisTaskV1.model_validate_json(files["task.json"])
    if _timestamp(task.task_accepted_at) > _timestamp(req.checked_at):
        raise ValueError("run_plan_task_from_future")
    hashes = {name: sha256(body).hexdigest() for name, body in files.items() if name != "index.json"}
    if saved != req.model_dump(mode="json") or json.loads(files["index.json"]) != hashes:
        raise ValueError("run_plan_mismatch")


def main(argv: list[str] | None = None) -> int:
    """Publish pending results normally; invalid inputs and publication failures exit one."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "prepare"):
        command = commands.add_parser(name)
        command.add_argument("--task", type=Path, required=True)
        command.add_argument("--checked-at", type=datetime.fromisoformat)
        command.add_argument("--config", type=Path, default=Path("config"))
        command.add_argument("--calendar", type=Path, required=True)
        command.add_argument("--calendar-metadata", type=Path, required=True)
        command.add_argument("--research-note", type=Path, required=True)
        command.add_argument("--output", type=Path, required=True)
        if name == "prepare":
            command.add_argument("--prices", type=Path, required=True)
            command.add_argument("--fx", type=Path, required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("--input", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            files = read_bundle(args.input)
            if "manifest.json" in files:
                validate_price_fx_run(files)
            else:
                validate_run_plan(files)
            print("price_fx_run: valid; offline_replay; analysis_ready=false")
            return 0
        checked = args.checked_at or datetime.now(UTC)
        task = DetailedAnalysisTaskV1.model_validate_json(_read_file(args.task))
        output = _safe_path(args.output)
        inputs = (args.task, args.config, args.calendar, args.calendar_metadata, args.research_note)
        if any(output == (path := _safe_path(p)) or output in path.parents or path in output.parents for p in inputs):
            raise ValueError("run_output_overlaps_input")
        if args.command == "prepare":
            result = prepare_price_fx_run(
                task=task,
                checked_at=checked,
                config=args.config,
                calendar=args.calendar,
                calendar_metadata=args.calendar_metadata,
                research_note=args.research_note,
                prices=args.prices,
                fx=args.fx,
                output=output,
            )
            print(
                json.dumps(
                    {
                        "status": result.status,
                        "analysis_ready": False,
                        "required_price_end": result.requirements.required_price_end,
                        "required_fx_end": result.requirements.required_fx_end,
                        "price_reasons": result.price.reasons,
                        "fx_reasons": result.fx.reasons,
                        "missing_price_dates": result.price.missing_dates,
                        "missing_fx_dates": result.fx.missing_dates,
                        "converted_count": result.converted_count,
                    }
                )
            )
        else:
            files = calendar_inputs(args.config, args.calendar, args.calendar_metadata, args.research_note)
            req = resolve_requirements(files, checked.isoformat())
            files["task.json"] = task.model_dump_json(indent=2).encode()
            files["requirements.json"] = req.model_dump_json(indent=2).encode()
            files["index.json"] = json.dumps(
                {name: sha256(body).hexdigest() for name, body in files.items()}, indent=2
            ).encode()
            publish_run_preparation(output.parent, output.name, files, validator=validate_run_plan)
            print(req.model_dump_json())
    except (ValueError, OSError, RuntimeError, KeyError) as error:
        print(f"price_fx_run_failed: {type(error).__name__}; no network or retry")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
