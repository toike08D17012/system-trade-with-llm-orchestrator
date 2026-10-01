"""Parse the public Toyota Global Newsroom article search response."""

import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from html import unescape
from urllib.parse import urlsplit


_TAG = re.compile(r"<[^>]+>")
_ALLOWED_HOSTS = {
    "global.toyota",
    "toyotatimes.jp",
    "toyotagazooracing.com",
    "toyota-racing-newsroom.com",
    "toyotamobilityfoundation.org",
    "www.toyotamobilityfoundation.org",
    "woven.toyota",
}


@dataclass(frozen=True)
class NewsroomEntry:
    """One dated candidate link from Toyota's newsroom listing."""

    contents_id: str
    published_on: date
    title: str
    url: str
    label_tags: tuple[str, ...]


def _date(value: str) -> date:
    return datetime.strptime(value.replace(".", ""), "%b %d, %Y").date()


def parse_newsroom_catalog(body: bytes, start: date, end: date) -> tuple[NewsroomEntry, ...]:
    """Require a listing that extends before the requested period."""
    if not 0 < len(body) <= 2_000_000 or start > end:
        raise ValueError("toyota_newsroom_input_invalid")
    try:
        payload = json.loads(body)
    except UnicodeDecodeError, json.JSONDecodeError:
        raise ValueError("toyota_newsroom_json_invalid") from None
    if not isinstance(payload, dict) or not isinstance(payload.get("list"), list):
        raise ValueError("toyota_newsroom_inventory_invalid")
    rows = payload["list"]
    if not rows or type(payload.get("totalCount")) is not int or payload["totalCount"] < len(rows):
        raise ValueError("toyota_newsroom_inventory_invalid")
    entries: list[NewsroomEntry] = []
    seen: set[str] = set()
    previous = date.max
    for row in rows:
        try:
            published_on = _date(row["date"])
            contents_id = row["contentsId"]
            title = " ".join(unescape(_TAG.sub(" ", row["title"])).split())
            url = row["url"]
            labels = row["labelTags"]
            parsed_url = urlsplit(url)
            host = parsed_url.hostname
        except KeyError, TypeError, ValueError, AttributeError:
            raise ValueError("toyota_newsroom_row_invalid") from None
        if (
            not title
            or not isinstance(contents_id, str)
            or not contents_id.isdigit()
            or not isinstance(url, str)
            or parsed_url.scheme not in {"http", "https"}
            or host not in _ALLOWED_HOSTS
            or not isinstance(labels, list)
            or not all(isinstance(label, str) for label in labels)
            or contents_id in seen
            or published_on > previous
        ):
            raise ValueError("toyota_newsroom_row_invalid")
        seen.add(contents_id)
        previous = published_on
        if start <= published_on <= end:
            entries.append(NewsroomEntry(contents_id, published_on, title, url, tuple(labels)))
    if rows and previous >= start:
        raise ValueError("toyota_newsroom_period_incomplete")
    if not entries:
        raise ValueError("toyota_newsroom_period_empty")
    return tuple(entries)
