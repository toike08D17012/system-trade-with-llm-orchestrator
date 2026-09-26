"""Opt-in preparation of one JPX-verified security's local price evidence."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from stock_research_llm_orchestrator.preparation.market_evidence import (
    CalendarInput,
    CalendarProvenance,
    JpxSnapshotProvenance,
    prepare_market_evidence,
)
from stock_research_llm_orchestrator.preparation.task_input import create_human_selected_task
from stock_research_llm_orchestrator.sources.yfinance.adapter import YfinanceDailyAdapter
from stock_research_llm_orchestrator.sources.yfinance.native_history import HistoryAcquisitionError
from stock_research_llm_orchestrator.sources.yfinance.normalization import three_year_start


def main(argv: list[str] | None = None) -> int:
    """Prepare three years through yesterday; report incomplete acceptance distinctly."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-network", action="store_true", required=True)
    parser.add_argument("--code", required=True)
    parser.add_argument("--evaluation-policy-version", type=int, required=True)
    parser.add_argument("--jpx-body", type=Path, required=True)
    parser.add_argument("--jpx-metadata", type=Path, required=True)
    parser.add_argument("--calendar", type=Path)
    parser.add_argument("--calendar-metadata", type=Path)
    parser.add_argument("--output", type=Path, required=True, help="New directory beneath an existing trusted parent")
    args = parser.parse_args(argv)
    if (args.calendar is None) != (args.calendar_metadata is None):
        parser.error("--calendar and --calendar-metadata must be supplied together")
    now = datetime.now(UTC)
    end = now.astimezone(ZoneInfo("Asia/Tokyo")).date()
    try:
        task = create_human_selected_task(
            args.code,
            args.evaluation_policy_version,
            mic="XTKS",
            task_id_factory=lambda: f"market-{uuid4().hex}",
            accepted_at_factory=lambda: now,
        )
        provenance = JpxSnapshotProvenance.model_validate_json(args.jpx_metadata.read_bytes())
        calendar = None
        if args.calendar is not None:
            calendar = CalendarInput(
                body=args.calendar.read_bytes(),
                provenance=CalendarProvenance.model_validate_json(args.calendar_metadata.read_bytes()),
            )
        prepared = prepare_market_evidence(
            task=task,
            jpx_body=args.jpx_body.read_bytes(),
            jpx_provenance=provenance,
            start=three_year_start(end),
            end=end,
            adapter=YfinanceDailyAdapter(),
            root=args.output.parent,
            destination_name=args.output.name,
            prepared_at=now,
            calendar=calendar,
        )
    except HistoryAcquisitionError as exc:
        print(
            json.dumps({"status": "acquisition_failed", "reason": str(exc), "retry_at": getattr(exc, "retry_at", None)})
        )
        return 1
    except OSError, ValueError:
        # Validation errors can contain source data; do not echo their input values.
        print(json.dumps({"status": "failed", "reason": "market_preparation_input_or_publication_invalid"}))
        return 1
    print(
        json.dumps(
            {
                "status": prepared.index.status,
                "analysis_ready": prepared.index.analysis_ready,
                "price_quality_passed": prepared.index.price_quality_passed,
                "issues": sorted({issue.code for issue in prepared.index.issues}),
                "artifact_count": len(prepared.receipt.files),
            }
        )
    )
    # Publication succeeded, but full live acceptance has unresolved checks.
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
