"""Tests for the Toyota newsroom candidate index."""

import json
from datetime import date

import pytest

from stock_research_llm_orchestrator.preparation.toyota_newsroom_catalog import (
    build_newsroom_bundle,
    validate_newsroom_bundle,
)
from stock_research_llm_orchestrator.sources.toyota_ir.newsroom_catalog import parse_newsroom_catalog


def _response() -> bytes:
    return json.dumps(
        {
            "totalCount": 3,
            "list": [
                {
                    "contentsId": "1",
                    "date": "Sep. 30, 2026",
                    "title": "Toyota <b>sales</b>",
                    "url": "https://global.toyota/en/newsroom/corporate/1.html",
                    "labelTags": ["Corporate"],
                },
                {
                    "contentsId": "2",
                    "date": "Oct. 01, 2025",
                    "title": "Toyota production",
                    "url": "https://global.toyota/en/newsroom/corporate/2.html",
                    "labelTags": [],
                },
                {
                    "contentsId": "3",
                    "date": "Sep. 30, 2025",
                    "title": "Previous period",
                    "url": "https://global.toyota/en/newsroom/corporate/3.html",
                    "labelTags": [],
                },
            ],
        }
    ).encode()


def test_newsroom_bundle_replays_period_and_source() -> None:
    """Keep candidate titles and dates bound to the saved response."""
    files = build_newsroom_bundle(_response(), date(2025, 10, 1), date(2026, 10, 1))
    validate_newsroom_bundle(files)
    catalog = json.loads(files["catalog.json"])
    assert len(catalog["entries"]) == 2
    assert catalog["entries"][0]["title"] == "Toyota sales"
    assert catalog["analysis_ready"] is False


def test_newsroom_rejects_period_without_older_boundary() -> None:
    """A capped response cannot imply full period coverage."""
    payload = json.loads(_response())
    payload["list"].pop()
    with pytest.raises(ValueError, match="period_incomplete"):
        parse_newsroom_catalog(json.dumps(payload).encode(), date(2025, 10, 1), date(2026, 10, 1))


def test_newsroom_rejects_wrong_domain_and_tampering() -> None:
    """Reject unknown hosts and modified raw bytes."""
    payload = json.loads(_response())
    payload["list"][0]["url"] = "https://example.com/news"
    with pytest.raises(ValueError, match="row_invalid"):
        parse_newsroom_catalog(json.dumps(payload).encode(), date(2025, 10, 1), date(2026, 10, 1))
    files = build_newsroom_bundle(_response(), date(2025, 10, 1), date(2026, 10, 1))
    files["response.json"] += b" "
    with pytest.raises(ValueError, match="binding_invalid"):
        validate_newsroom_bundle(files)
