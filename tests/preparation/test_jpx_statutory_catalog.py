"""JPX statutory filing bundle integrity tests."""

import json
from datetime import date

import pytest

from stock_research_llm_orchestrator.preparation.jpx_statutory_catalog import (
    build_statutory_bundle,
    validate_statutory_bundle,
)


def test_statutory_bundle_replays_original_and_rejects_changes() -> None:
    """The extracted filing ID must remain tied to its original HTML."""
    html = (
        "<h1>上場会社詳細（適時開示情報）</h1><td>72030</td>"
        '<tr id="hotei_0"><td>2026/09/11</td><td>'
        "<a onclick=\"displayPDF(Form, DisplayPDF, 'S100Z1LW');\">自己株券買付状況報告書</a></td></tr>"
    ).encode()
    files = build_statutory_bundle(html, date(2023, 10, 1), date(2026, 9, 30))
    validate_statutory_bundle(files)
    assert json.loads(files["catalog.json"])["entries"][0]["document_id"] == "S100Z1LW"
    changed = dict(files)
    changed["detail.html"] += b" "
    with pytest.raises(ValueError, match="binding_invalid"):
        validate_statutory_bundle(changed)
    changed = dict(files)
    catalog = json.loads(changed["catalog.json"])
    catalog["entries"][0]["document_id"] = "S100AAAA"
    changed["catalog.json"] = json.dumps(catalog).encode()
    with pytest.raises(ValueError, match="entries_invalid"):
        validate_statutory_bundle(changed)
