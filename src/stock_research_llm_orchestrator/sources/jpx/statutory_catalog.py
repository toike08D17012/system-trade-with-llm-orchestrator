"""Parse EDINET-linked statutory filing rows from a JPX company detail page."""

import re
from dataclasses import dataclass
from datetime import date
from html import unescape


_ROW = re.compile(r'<tr id="hotei_(\d+)">(.*?)</tr>', re.DOTALL)
_DATE = re.compile(r"<td[^>]*>\s*(20\d{2}/\d{2}/\d{2})\s*</td>")
_DOCUMENT = re.compile(r"displayPDF\([^;]*?'(S[0-9A-Z]{7})'\);", re.DOTALL)
_TITLE = re.compile(r"<a\b[^>]*>(.*?)</a>", re.DOTALL)
_TAG = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class StatutoryFilingEntry:
    """One issuer filing date and EDINET document ID listed by JPX."""

    submitted_on: date
    document_id: str
    title: str


def parse_statutory_catalog(body: bytes, start: date, end: date) -> tuple[StatutoryFilingEntry, ...]:
    """List the issuer's filing IDs without treating the JPX index as filing content."""
    if not 0 < len(body) <= 1_000_000 or start > end:
        raise ValueError("jpx_statutory_catalog_input_invalid")
    try:
        source = body.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("jpx_statutory_catalog_encoding_invalid") from None
    if "上場会社詳細（適時開示情報）" not in source or "<td>72030</td>" not in source:
        raise ValueError("jpx_statutory_catalog_issuer_invalid")
    entries: list[StatutoryFilingEntry] = []
    for row in _ROW.finditer(source):
        date_match = _DATE.search(row.group(2))
        if date_match is None:
            raise ValueError("jpx_statutory_catalog_date_missing")
        submitted_on = date.fromisoformat(date_match.group(1).replace("/", "-"))
        if not start <= submitted_on <= end:
            continue
        document = _DOCUMENT.search(row.group(2))
        title_match = _TITLE.search(row.group(2))
        if document is None or title_match is None:
            raise ValueError("jpx_statutory_catalog_document_missing")
        title = " ".join(unescape(_TAG.sub(" ", title_match.group(1))).split())
        if not title:
            raise ValueError("jpx_statutory_catalog_title_missing")
        entries.append(StatutoryFilingEntry(submitted_on, document.group(1), title))
    if not entries or len({entry.document_id for entry in entries}) != len(entries):
        raise ValueError("jpx_statutory_catalog_inventory_invalid")
    return tuple(entries)
