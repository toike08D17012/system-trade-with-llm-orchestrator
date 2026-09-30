"""Parse the dated document index on Toyota's fixed financial-results pages."""

import re
from dataclasses import dataclass
from datetime import date, datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit


CURRENT_URL = "https://global.toyota/en/ir/financial-results/"
ARCHIVE_URL = "https://global.toyota/en/ir/financial-results/archives/03.html"
MAX_HTML_BYTES = 1_000_000
_HEADING = re.compile(r"^(FY\d{4}(?: [1-3]Q)? Financial Results) \((\w+ \d{1,2}, \d{4})\)$")


@dataclass(frozen=True)
class CatalogEntry:
    """One link grouped under a dated financial-results heading."""

    published_on: date
    announcement: str
    title: str
    url: str
    page_url: str


class _CatalogParser(HTMLParser):
    def __init__(self, page_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.page_url = page_url
        self.entries: list[CatalogEntry] = []
        self.heading_depth = 0
        self.heading_parts: list[str] = []
        self.link_depth = 0
        self.link_parts: list[str] = []
        self.link_url: str | None = None
        self.announcement: str | None = None
        self.published_on: date | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "h3":
            self.heading_depth = 1
            self.heading_parts = []
            return
        if self.heading_depth:
            self.heading_depth += 1
        if tag == "a" and self.published_on is not None:
            self.link_depth = 1
            self.link_parts = []
            self.link_url = dict(attrs).get("href")
        elif self.link_depth:
            self.link_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if self.heading_depth:
            self.heading_depth -= 1
            if tag == "h3":
                heading = " ".join(" ".join(self.heading_parts).split())
                match = _HEADING.fullmatch(heading)
                self.announcement = match.group(1) if match else None
                self.published_on = datetime.strptime(match.group(2), "%B %d, %Y").date() if match else None
                self.heading_depth = 0
        if self.link_depth:
            self.link_depth -= 1
            if tag == "a":
                if self.link_url and self.announcement and self.published_on:
                    url = urljoin(self.page_url, self.link_url)
                    parsed = urlsplit(url)
                    if parsed.scheme != "https" or parsed.netloc != "global.toyota" or parsed.query or parsed.fragment:
                        raise ValueError("toyota_ir_catalog_link_invalid")
                    title = " ".join(" ".join(self.link_parts).split())
                    if title:
                        self.entries.append(
                            CatalogEntry(self.published_on, self.announcement, title, url, self.page_url)
                        )
                self.link_depth = 0

    def handle_data(self, data: str) -> None:
        if self.heading_depth:
            self.heading_parts.append(data)
        if self.link_depth:
            self.link_parts.append(data)


def parse_catalog(body: bytes, page_url: str, start: date, end: date) -> tuple[CatalogEntry, ...]:
    """Reject malformed or empty official indexes and return links in the requested period."""
    if page_url not in (CURRENT_URL, ARCHIVE_URL) or not 0 < len(body) <= MAX_HTML_BYTES or start > end:
        raise ValueError("toyota_ir_catalog_input_invalid")
    try:
        source = body.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("toyota_ir_catalog_encoding_invalid") from None
    parser = _CatalogParser(page_url)
    parser.feed(source)
    entries = tuple(entry for entry in parser.entries if start <= entry.published_on <= end)
    if not entries or len({(entry.published_on, entry.url) for entry in entries}) != len(entries):
        raise ValueError("toyota_ir_catalog_inventory_invalid")
    return entries
