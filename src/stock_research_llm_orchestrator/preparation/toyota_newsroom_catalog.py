"""Replay a private Toyota newsroom index against the saved API response."""

import argparse
import json
from collections.abc import Mapping
from datetime import date
from hashlib import sha256
from pathlib import Path

from stock_research_llm_orchestrator.preparation.storage import publish_preparation, read_bundle
from stock_research_llm_orchestrator.sources.toyota_ir.newsroom_catalog import parse_newsroom_catalog


_SOURCE_URL = "https://global.toyota/en/_system/api/search/article"


def _entries(body: bytes, start: date, end: date) -> list[dict[str, object]]:
    return [
        {
            "contents_id": entry.contents_id,
            "published_on": entry.published_on.isoformat(),
            "title": entry.title,
            "url": entry.url,
            "label_tags": list(entry.label_tags),
        }
        for entry in parse_newsroom_catalog(body, start, end)
    ]


def validate_newsroom_bundle(files: Mapping[str, bytes]) -> None:
    """Reparse the saved response and reject changed or partial indexes."""
    if set(files) != {"response.json", "catalog.json"}:
        raise ValueError("toyota_newsroom_bundle_inventory_invalid")
    catalog = json.loads(files["catalog.json"])
    start, end = date.fromisoformat(catalog["start"]), date.fromisoformat(catalog["end"])
    if (
        catalog["source_url"] != _SOURCE_URL
        or catalog["response_sha256"] != sha256(files["response.json"]).hexdigest()
        or catalog["response_byte_count"] != len(files["response.json"])
        or catalog["analysis_ready"] is not False
    ):
        raise ValueError("toyota_newsroom_bundle_binding_invalid")
    if catalog["entries"] != _entries(files["response.json"], start, end):
        raise ValueError("toyota_newsroom_bundle_entries_invalid")


def build_newsroom_bundle(body: bytes, start: date, end: date) -> dict[str, bytes]:
    """Bind candidate links to one saved Toyota newsroom response."""
    catalog = {
        "kind": "toyota-newsroom-candidate-index",
        "analysis_ready": False,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "source_url": _SOURCE_URL,
        "response_sha256": sha256(body).hexdigest(),
        "response_byte_count": len(body),
        "entries": _entries(body, start, end),
    }
    files = {"response.json": body, "catalog.json": json.dumps(catalog, ensure_ascii=False, indent=2).encode()}
    validate_newsroom_bundle(files)
    return files


def main(argv: list[str] | None = None) -> int:
    """Build from a saved search response or replay a private bundle."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("--response", type=Path, required=True)
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
                build_newsroom_bundle(args.response.read_bytes(), args.start, args.end),
                validator=validate_newsroom_bundle,
            )
            bundle = args.runs / args.destination
        else:
            bundle = args.bundle
        files = read_bundle(bundle)
        validate_newsroom_bundle(files)
        count = len(json.loads(files["catalog.json"])["entries"])
        print(f"toyota_newsroom_catalog: valid; articles={count}; analysis_ready=false")
    except (ValueError, OSError, RuntimeError, KeyError, TypeError) as error:
        print(f"toyota_newsroom_catalog_failed: {type(error).__name__}: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
