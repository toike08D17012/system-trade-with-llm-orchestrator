"""Replay a private Toyota JPX disclosure index against its saved HTML."""

import argparse
import json
from collections.abc import Mapping
from datetime import date
from hashlib import sha256
from pathlib import Path

from stock_research_llm_orchestrator.preparation.storage import publish_preparation, read_bundle
from stock_research_llm_orchestrator.sources.jpx.disclosure_catalog import DisclosureEntry, parse_disclosure_catalog


def _record(entry: DisclosureEntry) -> dict[str, str]:
    return {
        "disclosed_on": entry.disclosed_on.isoformat(),
        "category": entry.category,
        "title": entry.title,
        "url": entry.url,
    }


def validate_disclosure_bundle(files: Mapping[str, bytes]) -> None:
    """Require the precise original HTML and every derived disclosure row."""
    if set(files) != {"detail.html", "catalog.json"}:
        raise ValueError("jpx_disclosure_bundle_inventory_invalid")
    catalog = json.loads(files["catalog.json"])
    start, end = date.fromisoformat(catalog["start"]), date.fromisoformat(catalog["end"])
    if (
        start > end
        or catalog["source_url"] != "https://www2.jpx.co.jp/tseHpFront/JJK010030Action.do"
        or catalog["html_sha256"] != sha256(files["detail.html"]).hexdigest()
        or catalog["html_byte_count"] != len(files["detail.html"])
        or catalog["analysis_ready"] is not False
    ):
        raise ValueError("jpx_disclosure_bundle_binding_invalid")
    entries = [_record(entry) for entry in parse_disclosure_catalog(files["detail.html"], start, end)]
    if catalog["entries"] != entries or catalog["unique_document_count"] != len({item["url"] for item in entries}):
        raise ValueError("jpx_disclosure_bundle_entries_invalid")


def build_disclosure_bundle(html: bytes, start: date, end: date) -> dict[str, bytes]:
    """Keep the parsed rows and the original JPX company page together."""
    entries = [_record(entry) for entry in parse_disclosure_catalog(html, start, end)]
    catalog = {
        "kind": "jpx-toyota-timely-disclosure-index",
        "analysis_ready": False,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "source_url": "https://www2.jpx.co.jp/tseHpFront/JJK010030Action.do",
        "html_sha256": sha256(html).hexdigest(),
        "html_byte_count": len(html),
        "unique_document_count": len({item["url"] for item in entries}),
        "entries": entries,
    }
    files = {"detail.html": html, "catalog.json": json.dumps(catalog, ensure_ascii=False, indent=2).encode()}
    validate_disclosure_bundle(files)
    return files


def main(argv: list[str] | None = None) -> int:
    """Build from saved HTML or validate an existing private index."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("--html", type=Path, required=True)
    build.add_argument("--runs", type=Path, required=True)
    build.add_argument("--destination", required=True)
    build.add_argument("--start", type=date.fromisoformat, required=True)
    build.add_argument("--end", type=date.fromisoformat, required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("--bundle", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            publish_preparation(
                args.runs,
                args.destination,
                build_disclosure_bundle(args.html.read_bytes(), args.start, args.end),
                validator=validate_disclosure_bundle,
            )
            bundle = args.runs / args.destination
        else:
            bundle = args.bundle
        files = read_bundle(bundle)
        validate_disclosure_bundle(files)
        result = json.loads(files["catalog.json"])
        print(
            f"jpx_disclosure_catalog: valid; rows={len(result['entries'])}; "
            f"documents={result['unique_document_count']}; analysis_ready=false"
        )
    except (ValueError, OSError, RuntimeError, KeyError, TypeError) as error:
        print(f"jpx_disclosure_catalog_failed: {type(error).__name__}: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
