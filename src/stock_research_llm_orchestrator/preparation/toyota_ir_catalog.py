"""Build and replay a private Toyota financial-results link inventory from saved HTML."""

import json
from collections.abc import Mapping
from datetime import date
from hashlib import sha256
from pathlib import Path

from stock_research_llm_orchestrator.preparation.storage import publish_preparation, read_bundle
from stock_research_llm_orchestrator.sources.toyota_ir.catalog import ARCHIVE_URL, CURRENT_URL, parse_catalog


def validate_catalog_bundle(files: Mapping[str, bytes]) -> None:
    """Reparse both original pages and reject any changed index or derived entry."""
    if set(files) != {"current.html", "archive.html", "catalog.json"}:
        raise ValueError("toyota_ir_catalog_bundle_inventory_invalid")
    catalog = json.loads(files["catalog.json"])
    start, end = date.fromisoformat(catalog["start"]), date.fromisoformat(catalog["end"])
    if start > end:
        raise ValueError("toyota_ir_catalog_period_invalid")
    pages = (("current.html", CURRENT_URL), ("archive.html", ARCHIVE_URL))
    if catalog["pages"] != [
        {"url": url, "sha256": sha256(files[name]).hexdigest(), "byte_count": len(files[name])} for name, url in pages
    ]:
        raise ValueError("toyota_ir_catalog_page_hash_invalid")
    entries = [
        {
            "published_on": item.published_on.isoformat(),
            "announcement": item.announcement,
            "title": item.title,
            "url": item.url,
            "page_url": item.page_url,
        }
        for name, url in pages
        for item in parse_catalog(files[name], url, start, end)
    ]
    if catalog["entries"] != entries or catalog["analysis_ready"] is not False:
        raise ValueError("toyota_ir_catalog_entries_invalid")


def build_catalog_bundle(current: bytes, archive: bytes, start: date, end: date) -> dict[str, bytes]:
    """Keep source HTML beside the derived link list for offline review."""
    pages = (("current.html", CURRENT_URL, current), ("archive.html", ARCHIVE_URL, archive))
    entries = [
        {
            "published_on": item.published_on.isoformat(),
            "announcement": item.announcement,
            "title": item.title,
            "url": item.url,
            "page_url": item.page_url,
        }
        for _, url, body in pages
        for item in parse_catalog(body, url, start, end)
    ]
    catalog = {
        "kind": "toyota-financial-results-link-inventory",
        "analysis_ready": False,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "pages": [{"url": url, "sha256": sha256(body).hexdigest(), "byte_count": len(body)} for _, url, body in pages],
        "entries": entries,
    }
    files = {"current.html": current, "archive.html": archive, "catalog.json": json.dumps(catalog, indent=2).encode()}
    validate_catalog_bundle(files)
    return files


def publish_catalog_bundle(
    runs: Path, destination: str, current: bytes, archive: bytes, start: date, end: date
) -> None:
    """Publish one immutable private bundle containing the complete page bytes."""
    publish_preparation(
        runs, destination, build_catalog_bundle(current, archive, start, end), validator=validate_catalog_bundle
    )


def validate_saved_catalog(path: Path) -> int:
    """Validate a published bundle without network access."""
    files = read_bundle(path)
    validate_catalog_bundle(files)
    return len(json.loads(files["catalog.json"])["entries"])
