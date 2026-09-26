"""Revalidate a retained market slice against supplied local research evidence."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from stock_research_llm_orchestrator.preparation.market_revalidation import revalidate_market_evidence


def main(argv: list[str] | None = None) -> int:
    """Publish offline findings; exit 2 for retained gaps and 1 for technical failure."""
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("preparation", "calendar", "calendar-metadata", "research-note", "output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--publication-observation", type=Path)
    args = parser.parse_args(argv)
    try:
        index = revalidate_market_evidence(
            preparation=args.preparation,
            calendar=args.calendar,
            calendar_metadata=args.calendar_metadata,
            research_note=args.research_note,
            publication_observation=args.publication_observation,
            output=args.output,
            checked_at=datetime.now(UTC),
        )
    except OSError, ValueError, KeyError:
        print(json.dumps({"status": "failed", "reason": "market_revalidation_input_or_publication_invalid"}))
        return 1
    print(
        json.dumps(
            {
                "status": index.status,
                "analysis_ready": index.analysis_ready,
                "scheduled_dates_match": index.findings.scheduled_dates_match,
                "latest_published_month_match": index.findings.latest_published_month_match,
                "missing_count": len(index.findings.missing_dates),
                "unexpected_count": len(index.findings.unexpected_dates),
                "artifact_count": len(index.files) + 1,
            }
        )
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
