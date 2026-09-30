"""JPX Toyota disclosure index parser tests."""

from datetime import date

import pytest

from stock_research_llm_orchestrator.sources.jpx.disclosure_catalog import parse_disclosure_catalog


HEADER = "<h1>上場会社詳細（適時開示情報）</h1><td>72030</td>"
ROW = '<tr id="1101_0"><td align="center">2026/08/07</td><td><a href="/disc/72030/a.pdf">訂正 <b>資料</b></a></td></tr>'


def test_jpx_disclosure_catalog_extracts_dated_correction() -> None:
    """A dated correction keeps its category and official PDF URL."""
    entries = parse_disclosure_catalog((HEADER + ROW).encode(), date(2023, 10, 1), date(2026, 9, 30))
    assert len(entries) == 1
    assert entries[0].disclosed_on == date(2026, 8, 7)
    assert entries[0].category == "financial-results"
    assert entries[0].title == "訂正 資料"
    assert entries[0].url == "https://www2.jpx.co.jp/disc/72030/a.pdf"


def test_jpx_disclosure_catalog_rejects_wrong_issuer_and_missing_link() -> None:
    """Wrong issuer or absent document link cannot establish coverage."""
    with pytest.raises(ValueError, match="issuer_invalid"):
        parse_disclosure_catalog(ROW.encode(), date(2023, 10, 1), date(2026, 9, 30))
    with pytest.raises(ValueError, match="row_link_missing"):
        parse_disclosure_catalog(
            (HEADER + ROW.replace("/disc/72030/a.pdf", "https://example.com/a.pdf")).encode(),
            date(2023, 10, 1),
            date(2026, 9, 30),
        )
