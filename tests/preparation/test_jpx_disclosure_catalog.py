"""JPX disclosure bundle byte-binding tests."""

import json
from datetime import date

import pytest

from stock_research_llm_orchestrator.preparation.jpx_disclosure_catalog import (
    build_disclosure_bundle,
    validate_disclosure_bundle,
)


def test_disclosure_bundle_replays_raw_and_detects_tampering() -> None:
    """Changes to raw HTML or the extracted row invalidate the bundle."""
    html = (
        "<h1>上場会社詳細（適時開示情報）</h1><td>72030</td>"
        '<tr id="1102_0"><td>2026/08/04</td><td><a href="/disc/72030/a.pdf">自己株式取得</a></td></tr>'
    ).encode()
    files = build_disclosure_bundle(html, date(2023, 10, 1), date(2026, 9, 30))
    validate_disclosure_bundle(files)
    assert json.loads(files["catalog.json"])["unique_document_count"] == 1
    changed = dict(files)
    changed["detail.html"] += b" "
    with pytest.raises(ValueError, match="binding_invalid"):
        validate_disclosure_bundle(changed)
    changed = dict(files)
    catalog = json.loads(changed["catalog.json"])
    catalog["entries"][0]["title"] = "Other"
    changed["catalog.json"] = json.dumps(catalog).encode()
    with pytest.raises(ValueError, match="entries_invalid"):
        validate_disclosure_bundle(changed)
