"""Offline Toyota index bundle remains tied to its original HTML bytes."""

import json
from datetime import date

import pytest

from stock_research_llm_orchestrator.preparation.toyota_ir_catalog import (
    build_catalog_bundle,
    validate_catalog_bundle,
)


def test_catalog_bundle_replays_and_detects_changed_html_or_entries() -> None:
    """Source bytes and derived entries are both replay-validated."""
    current = b'<h3>FY2027 1Q Financial Results (August 4, 2026)</h3><a href="/a.pdf">A</a>'
    archive = b'<h3>FY2026 Financial Results (May 8, 2026)</h3><a href="/b.pdf">B</a>'
    files = build_catalog_bundle(current, archive, date(2023, 10, 1), date(2026, 9, 30))
    validate_catalog_bundle(files)
    assert len(json.loads(files["catalog.json"])["entries"]) == 2
    changed = dict(files)
    changed["current.html"] += b" "
    with pytest.raises(ValueError, match="page_hash_invalid"):
        validate_catalog_bundle(changed)
    changed = dict(files)
    catalog = json.loads(changed["catalog.json"])
    catalog["entries"][0]["title"] = "Changed"
    changed["catalog.json"] = json.dumps(catalog).encode()
    with pytest.raises(ValueError, match="entries_invalid"):
        validate_catalog_bundle(changed)
