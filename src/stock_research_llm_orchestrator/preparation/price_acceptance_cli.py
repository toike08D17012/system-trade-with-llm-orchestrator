"""Evaluate fixed-period local price evidence without external acquisition."""

import argparse
import csv
import json
from datetime import UTC, datetime
from pathlib import Path

import yaml

from stock_research_llm_orchestrator.preparation.price_acceptance import accept_price_evidence


def main(argv: list[str] | None = None) -> int:
    """Exit 0 for price acceptance, 2 for pending, and 1 for technical failure."""
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("preparation", "calendar", "calendar-metadata", "research-note", "output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--approval-root", type=Path, default=Path("config/source-approvals"))
    parser.add_argument("--known-conflict", type=Path)
    parser.add_argument("--conflict-evidence", type=Path)
    args = parser.parse_args(argv)
    try:
        result = accept_price_evidence(
            preparation=args.preparation,
            calendar=args.calendar,
            calendar_metadata=args.calendar_metadata,
            research_note=args.research_note,
            output=args.output,
            checked_at=datetime.now(UTC),
            approval_root=args.approval_root,
            known_conflict=args.known_conflict,
            conflict_evidence=args.conflict_evidence,
        )
    except OSError, ValueError, KeyError, TypeError, csv.Error, yaml.YAMLError:
        print(json.dumps({"status": "failed", "reason": "price_acceptance_input_or_publication_invalid"}))
        return 1
    print(
        json.dumps(
            {
                "status": result.status,
                "scope": result.scope,
                "analysis_ready": result.analysis_ready,
                "failed_conditions": [
                    condition.condition_id for condition in result.conditions if not condition.passed
                ],
                "limitations": result.limitations,
                "restricted_uses": result.restricted_uses,
            }
        )
    )
    return 2 if result.status == "pending" else 0


if __name__ == "__main__":
    raise SystemExit(main())
