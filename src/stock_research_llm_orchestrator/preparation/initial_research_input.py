"""Build a self-contained provisional input from retained market preparation."""

import argparse
import json
from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path

from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.task import DetailedAnalysisTaskV1
from stock_research_llm_orchestrator.preparation.market_evidence import (
    JpxSnapshotProvenance,
    MarketPreparationIndex,
    validate_market_preparation,
)
from stock_research_llm_orchestrator.preparation.research_draft import DraftEvidence, DraftInput
from stock_research_llm_orchestrator.preparation.storage import publish_preparation, read_bundle
from stock_research_llm_orchestrator.sources.yfinance.normalization import NormalizedPrices


def _build(source: Mapping[str, bytes]) -> dict[str, bytes]:
    """Recreate every derived byte from an independently validated market slice."""
    validate_market_preparation(source)
    task = DetailedAnalysisTaskV1.model_validate_json(source["task.json"])
    index = MarketPreparationIndex.model_validate_json(source["index.json"])
    jpx = JpxSnapshotProvenance.model_validate_json(source["jpx-metadata.json"])
    history = json.loads(source["history-metadata.json"])
    prices = NormalizedPrices.model_validate_json(source["normalized.json"])
    symbol = history["symbol"]
    if not isinstance(symbol, str) or not symbol:
        raise ValueError("initial_research_symbol_missing")
    jpx_text = json.dumps(
        {
            "verification": index.jpx_verification.model_dump(mode="json"),
            "snapshot_freshness": index.jpx_freshness,
            "note": "Eligibility is verified only for the retained JPX snapshot date.",
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    price_text = json.dumps(
        {
            "provenance": {
                "provider": history["source"],
                "library": history["library"],
                "library_version": history["library_version"],
                "function": history["function"],
                "symbol": symbol,
                "http_body_retained": history["http_body_retained"],
                "returned_table_sha256": sha256(source["prices.csv"]).hexdigest(),
            },
            "provider_identity_verified": index.provider_identity_verified,
            "quality_passed": index.price_quality_passed,
            "issues": [issue.model_dump(mode="json") for issue in index.issues],
            "limitations": list(index.limitations),
            "normalized_prices": prices.model_dump(mode="json"),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    data = DraftInput(
        security_code=task.security.security_code,
        exchange=task.security.mic,
        external_llm_transfer_allowed=True,
        evidence=(
            DraftEvidence(
                evidence_id="jpx-listing-snapshot",
                source_id="jpx",
                approval_version=2,
                source_url=jpx.reference,
                source_sha256=sha256(source["jpx.xlsx"]).hexdigest(),
                retrieved_at=jpx.retrieved_at,
                text=jpx_text,
            ),
            DraftEvidence(
                evidence_id="yfinance-returned-prices",
                source_id="yfinance",
                approval_version=3,
                source_reference=f"yfinance:Ticker.history:{symbol}",
                source_sha256=sha256(source["prices.csv"]).hexdigest(),
                retrieved_at=history["retrieved_at"],
                text=price_text,
            ),
        ),
    )
    input_bytes = data.model_dump_json(indent=2).encode() + b"\n"
    manifest = {
        "kind": "initial-research-input",
        "analysis_ready": False,
        "market_index_sha256": sha256(source["index.json"]).hexdigest(),
        "input_sha256": sha256(input_bytes).hexdigest(),
        "evidence_text_sha256": {item.evidence_id: sha256(item.text.encode()).hexdigest() for item in data.evidence},
    }
    return {
        **{f"market/{name}": body for name, body in source.items()},
        "input.json": input_bytes,
        "manifest.json": (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode(),
    }


def build_initial_research_input(source: Mapping[str, bytes]) -> dict[str, bytes]:
    """Build an offline-replayable first-pass input."""
    return _build(source)


def validate_initial_research_input(files: Mapping[str, bytes]) -> None:
    """Rebuild the input and its citations from retained source bytes."""
    source = {name.removeprefix("market/"): body for name, body in files.items() if name.startswith("market/")}
    if dict(files) != _build(source):
        raise ValueError("initial_research_input_binding_invalid")


def main(argv: list[str] | None = None) -> int:
    """Prepare or verify an initial provisional research input."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--market", type=Path, required=True)
    prepare.add_argument("--runs", type=Path, required=True)
    prepare.add_argument("--destination", required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("--bundle", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            files = build_initial_research_input(read_bundle(args.market))
            publish_preparation(args.runs, args.destination, files, validator=validate_initial_research_input)
            bundle = args.runs / args.destination
        else:
            bundle = args.bundle
        validate_initial_research_input(read_bundle(bundle))
        print(f"initial_research_input: valid; input={bundle / 'input.json'}; analysis_ready=false")
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(f"initial_research_input_failed: {type(error).__name__}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
