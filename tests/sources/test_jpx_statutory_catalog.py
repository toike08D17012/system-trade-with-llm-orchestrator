"""JPX-listed EDINET filing index parsing contracts."""

from datetime import date

import pytest

from stock_research_llm_orchestrator.sources.jpx.statutory_catalog import parse_statutory_catalog


HEADER = "<h1>上場会社詳細（適時開示情報）</h1><td>72030</td>"
ROW = (
    '<tr id="hotei_0"><td align="center">2026/09/11</td><td>'
    '<a href="javascript:void(0);" onclick="displayPDF(document.forms[\'Form\'], '
    "document.forms['Form'].DisplayPDF, 'S100Z1LW');\">自己株券買付状況報告書</a></td></tr>"
)


def test_statutory_catalog_extracts_issuer_document_id() -> None:
    """The issuer filing row carries its EDINET ID and filing date."""
    entries = parse_statutory_catalog((HEADER + ROW).encode(), date(2023, 10, 1), date(2026, 9, 30))
    assert len(entries) == 1
    assert entries[0].document_id == "S100Z1LW"
    assert entries[0].submitted_on == date(2026, 9, 11)


def test_statutory_catalog_rejects_wrong_issuer_and_missing_document() -> None:
    """A filing ID cannot be accepted without its Toyota issuer context."""
    with pytest.raises(ValueError, match="issuer_invalid"):
        parse_statutory_catalog(ROW.encode(), date(2023, 10, 1), date(2026, 9, 30))
    with pytest.raises(ValueError, match="document_missing"):
        parse_statutory_catalog(
            (HEADER + ROW.replace("S100Z1LW", "INVALID")).encode(),
            date(2023, 10, 1),
            date(2026, 9, 30),
        )
