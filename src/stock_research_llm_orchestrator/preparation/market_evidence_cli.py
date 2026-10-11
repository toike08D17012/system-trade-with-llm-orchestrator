"""Opt-in preparation of one JPX-verified security's local price evidence."""

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.evidence import EvidenceSetV1
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.task import DetailedAnalysisTaskV1
from stock_research_llm_orchestrator.preparation.evidence_freeze import validate_frozen_market
from stock_research_llm_orchestrator.preparation.evidence_update import validate_jpx_evidence_update
from stock_research_llm_orchestrator.preparation.market_evidence import (
    CalendarInput,
    CalendarProvenance,
    JpxSnapshotProvenance,
    prepare_market_evidence,
)
from stock_research_llm_orchestrator.preparation.storage import read_bundle
from stock_research_llm_orchestrator.preparation.task_input import create_human_selected_task
from stock_research_llm_orchestrator.sources.yfinance.adapter import YfinanceDailyAdapter
from stock_research_llm_orchestrator.sources.yfinance.native_history import HistoryAcquisitionError
from stock_research_llm_orchestrator.sources.yfinance.normalization import three_year_start


def _continued_inputs(bundle_path: Path) -> tuple[DetailedAnalysisTaskV1, bytes, JpxSnapshotProvenance]:
    """Replay a frozen bundle before reusing its task and latest JPX acquisition."""
    bundle = read_bundle(bundle_path)
    evidence = EvidenceSetV1.model_validate_json(bundle["evidence-set.json"])
    if evidence.evidence_set_version == 1:
        validate_frozen_market(bundle)
        prefix = "market/"
    elif evidence.evidence_set_version == 2:
        validate_jpx_evidence_update(bundle)
        prefix = "follow-up/"
    else:
        raise ValueError("market_continue_evidence_version_unsupported")
    task_path = "market/task.json" if evidence.evidence_set_version == 1 else "previous/market/task.json"
    workbook_path = "jpx.xlsx" if evidence.evidence_set_version == 1 else "jpx-workbook.xlsx"
    provenance_path = "jpx-metadata.json" if evidence.evidence_set_version == 1 else "jpx-provenance.json"
    return (
        DetailedAnalysisTaskV1.model_validate_json(bundle[task_path]),
        bundle[f"{prefix}{workbook_path}"],
        JpxSnapshotProvenance.model_validate_json(bundle[f"{prefix}{provenance_path}"]),
    )


def main(argv: list[str] | None = None) -> int:
    """Prepare three years through yesterday or the completed current session."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-network", action="store_true", required=True)
    parser.add_argument("--include-today", action="store_true", help="Include today's completed Tokyo session")
    parser.add_argument("--continue-evidence", type=Path)
    parser.add_argument("--code")
    parser.add_argument("--evaluation-policy-version", type=int)
    parser.add_argument("--jpx-body", type=Path)
    parser.add_argument("--jpx-metadata", type=Path)
    parser.add_argument("--calendar", type=Path)
    parser.add_argument("--calendar-metadata", type=Path)
    parser.add_argument("--output", type=Path, required=True, help="New directory beneath an existing trusted parent")
    args = parser.parse_args(argv)
    if (args.calendar is None) != (args.calendar_metadata is None):
        parser.error("--calendar and --calendar-metadata must be supplied together")
    new_fields = (args.code, args.evaluation_policy_version, args.jpx_body, args.jpx_metadata)
    if (args.continue_evidence is None and any(value is None for value in new_fields)) or (
        args.continue_evidence is not None and any(value is not None for value in new_fields)
    ):
        parser.error("supply either --continue-evidence or all new-task source arguments")
    now = datetime.now(UTC)
    end = now.astimezone(ZoneInfo("Asia/Tokyo")).date() + timedelta(days=args.include_today)
    try:
        if args.continue_evidence is not None:
            task, jpx_body, provenance = _continued_inputs(args.continue_evidence)
            if datetime.fromisoformat(task.task_accepted_at.replace("Z", "+00:00")) > now:
                raise ValueError("market_continue_task_in_future")
        else:
            task = create_human_selected_task(
                args.code,
                args.evaluation_policy_version,
                mic="XTKS",
                task_id_factory=lambda: f"market-{uuid4().hex}",
                accepted_at_factory=lambda: now,
            )
            jpx_body = args.jpx_body.read_bytes()
            provenance = JpxSnapshotProvenance.model_validate_json(args.jpx_metadata.read_bytes())
        calendar = None
        if args.calendar is not None:
            calendar = CalendarInput(
                body=args.calendar.read_bytes(),
                provenance=CalendarProvenance.model_validate_json(args.calendar_metadata.read_bytes()),
            )
        prepared = prepare_market_evidence(
            task=task,
            jpx_body=jpx_body,
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
