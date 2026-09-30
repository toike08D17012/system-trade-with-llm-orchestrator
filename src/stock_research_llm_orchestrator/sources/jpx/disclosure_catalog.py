"""Parse Toyota timely-disclosure rows from the JPX listed-company detail page."""

import re
from dataclasses import dataclass
from datetime import date
from html import unescape
from urllib.parse import urljoin


DETAIL_ORIGIN = "https://www2.jpx.co.jp"
MAX_HTML_BYTES = 1_000_000
_ROW = re.compile(r'<tr id="(110[1-4])_(\d+)">(.*?)</tr>', re.DOTALL)
_DATE = re.compile(r"<td[^>]*>\s*(20\d{2}/\d{2}/\d{2})\s*</td>")
_LINK = re.compile(r'<a href="(/disc/72030/[a-zA-Z0-9_.-]+\.pdf)"[^>]*>(.*?)</a>', re.DOTALL)
_TAG = re.compile(r"<[^>]+>")
CATEGORIES = {
    "1101": "financial-results",
    "1102": "decisions-and-events",
    "1103": "listing-standards",
    "1104": "other",
}


@dataclass(frozen=True)
class DisclosureEntry:
    """One dated official disclosure link, before document-content adoption."""

    disclosed_on: date
    category: str
    title: str
    url: str


def parse_disclosure_catalog(body: bytes, start: date, end: date) -> tuple[DisclosureEntry, ...]:
    """Extract all four JPX timely-disclosure categories for Toyota in a fixed period."""
    if not 0 < len(body) <= MAX_HTML_BYTES or start > end:
        raise ValueError("jpx_disclosure_catalog_input_invalid")
    try:
        source = body.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("jpx_disclosure_catalog_encoding_invalid") from None
    if "上場会社詳細（適時開示情報）" not in source or "<td>72030</td>" not in source:
        raise ValueError("jpx_disclosure_catalog_issuer_invalid")
    entries: list[DisclosureEntry] = []
    for match in _ROW.finditer(source):
        category, _, row = match.groups()
        date_match = _DATE.search(row)
        if date_match is None:
            raise ValueError("jpx_disclosure_catalog_row_date_missing")
        disclosed_on = date.fromisoformat(date_match.group(1).replace("/", "-"))
        if not start <= disclosed_on <= end:
            continue
        link_match = _LINK.search(row)
        if link_match is None:
            raise ValueError("jpx_disclosure_catalog_row_link_missing")
        title = " ".join(unescape(_TAG.sub(" ", link_match.group(2))).split())
        if not title:
            raise ValueError("jpx_disclosure_catalog_row_title_missing")
        entries.append(
            DisclosureEntry(
                disclosed_on=disclosed_on,
                category=CATEGORIES[category],
                title=title,
                url=urljoin(DETAIL_ORIGIN, link_match.group(1)),
            )
        )
    if not entries or len({(entry.category, entry.disclosed_on, entry.url) for entry in entries}) != len(entries):
        raise ValueError("jpx_disclosure_catalog_inventory_invalid")
    return tuple(entries)
