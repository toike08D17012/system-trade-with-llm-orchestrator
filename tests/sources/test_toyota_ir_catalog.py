"""Toyota financial-results index parsing contracts."""

from datetime import date

import pytest

from stock_research_llm_orchestrator.sources.toyota_ir.catalog import ARCHIVE_URL, CURRENT_URL, parse_catalog


def test_parse_catalog_extracts_nested_titles_and_dated_links() -> None:
    """Nested heading and link labels retain their announcement date."""
    body = b"""
    <h3>FY2027 1Q Financial Results <span>(August 4, 2026)</span></h3>
    <ul><li><a href="/pages/global_toyota/ir/financial-results/a.pdf">Financial <span>Summary</span></a></li></ul>
    <h3>Click for more</h3><a href="/en/ir/financial-results/archives/03.html">Archive</a>
    """
    entries = parse_catalog(body, CURRENT_URL, date(2023, 10, 1), date(2026, 9, 30))
    assert len(entries) == 1
    assert entries[0].published_on == date(2026, 8, 4)
    assert entries[0].title == "Financial Summary"
    assert entries[0].url == "https://global.toyota/pages/global_toyota/ir/financial-results/a.pdf"


def test_parse_catalog_rejects_external_and_duplicate_links() -> None:
    """A listed link cannot escape the official host or repeat its identity."""
    heading = b"<h3>FY2026 Financial Results (May 8, 2026)</h3>"
    external = heading + b'<a href="https://example.com/a.pdf">Summary</a>'
    with pytest.raises(ValueError, match="link_invalid"):
        parse_catalog(external, ARCHIVE_URL, date(2023, 10, 1), date(2026, 9, 30))
    duplicate = heading + b'<a href="/a.pdf">A</a><a href="/a.pdf">A</a>'
    with pytest.raises(ValueError, match="inventory_invalid"):
        parse_catalog(duplicate, ARCHIVE_URL, date(2023, 10, 1), date(2026, 9, 30))


def test_parse_catalog_rejects_empty_scope_and_bad_encoding() -> None:
    """Empty or undecodable pages cannot be treated as complete inventory."""
    with pytest.raises(ValueError, match="inventory_invalid"):
        parse_catalog(b"<h3>Click for more</h3>", CURRENT_URL, date(2023, 10, 1), date(2026, 9, 30))
    with pytest.raises(ValueError, match="encoding_invalid"):
        parse_catalog(b"\xff", CURRENT_URL, date(2023, 10, 1), date(2026, 9, 30))
