"""Pure parser for the official JPX current listed-issues workbook."""

import io
import re
import zipfile
from datetime import date
from typing import Literal
from xml.etree import ElementTree

from pydantic import Field, model_validator

from stock_research_llm_orchestrator.contracts.base import StrictContractModel
from stock_research_llm_orchestrator.sources.protocol import (
    BoundedSourceResponse,
    CredentialFreeSourceIntent,
    SourceParameter,
)


_NS = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
_HEADERS = (
    "日付",
    "コード",
    "銘柄名",
    "市場・商品区分",
    "33業種コード",
    "33業種区分",
    "17業種コード",
    "17業種区分",
    "規模コード",
    "規模区分",
)
_ELIGIBLE_MARKETS: dict[str, Literal["Prime", "Standard", "Growth"]] = {
    "プライム（内国株式）": "Prime",
    "スタンダード（内国株式）": "Standard",
    "グロース（内国株式）": "Growth",
}
_CELL = re.compile(r"^([A-J])([1-9][0-9]*)$")


class JpxCurrentListParseError(ValueError):
    """Sanitized failure raised for an invalid JPX workbook."""


class JpxListedIssue(StrictContractModel):
    """One source-native current listed issue."""

    snapshot_on: str
    code: str = Field(pattern=r"^[0-9A-Z]{4}$")
    name: str
    market_product_category: str
    industry_33_code: str | None = None
    industry_33_name: str | None = None
    industry_17_code: str | None = None
    industry_17_name: str | None = None
    scale_code: str | None = None
    scale_name: str | None = None
    mic: Literal["XTKS"] = "XTKS"
    issuer_domesticity: Literal["domestic", "unknown"]
    security_class: Literal["ordinary_common_equity", "unknown"]
    eligibility: Literal["eligible", "ineligible", "unknown"]
    market_segment: Literal["Prime", "Standard", "Growth"] | None


class JpxCurrentList(StrictContractModel):
    """One complete month-end JPX current-list snapshot."""

    snapshot_on: str
    issues: tuple[JpxListedIssue, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def require_canonical_snapshot(self) -> JpxCurrentList:
        """Require one date and stable unique issue codes."""
        date.fromisoformat(self.snapshot_on)
        codes = tuple(issue.code for issue in self.issues)
        if len(codes) != len(set(codes)) or codes != tuple(sorted(codes)):
            raise ValueError("jpx_issue_codes_not_canonical")
        if any(issue.snapshot_on != self.snapshot_on for issue in self.issues):
            raise ValueError("jpx_snapshot_mismatch")
        return self


class JpxCurrentListAdapter:
    """Build and parse the approved fixed JPX current-list resource."""

    @property
    def source_id(self) -> str:
        """Return the immutable source identifier."""
        return "jpx"

    def build_intent(self, operation: str, parameters: tuple[SourceParameter, ...]) -> CredentialFreeSourceIntent:
        """Build the parameter-free current-list request."""
        if operation != "current-listed-issues" or parameters:
            raise ValueError("invalid_jpx_current_list_intent")
        return CredentialFreeSourceIntent(
            source_id="jpx",
            operation=operation,
            origin="www.jpx.co.jp",
            resource_key="tse-current-listed-issues-xlsx",
        )

    def parse(self, response: BoundedSourceResponse) -> JpxCurrentList:
        """Parse XLSX without formulas, macros, or external relationships."""
        if response.media_type != "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":
            raise JpxCurrentListParseError("jpx_current_list_invalid")
        try:
            with zipfile.ZipFile(io.BytesIO(response.body)) as archive:
                names = set(archive.namelist())
                required = {"xl/sharedStrings.xml", "xl/worksheets/sheet1.xml"}
                if not required <= names or any(name.endswith("vbaProject.bin") for name in names):
                    raise ValueError
                if any(name.startswith("/") or ".." in name.split("/") for name in names):
                    raise ValueError
                shared = _shared_strings(archive.read("xl/sharedStrings.xml"))
                rows = _rows(archive.read("xl/worksheets/sheet1.xml"), shared)
            if not rows or tuple(rows[0]) != _HEADERS:
                raise ValueError
            issues = tuple(_issue(row) for row in rows[1:])
            snapshots = {issue.snapshot_on for issue in issues}
            if len(snapshots) != 1:
                raise ValueError
            return JpxCurrentList(snapshot_on=snapshots.pop(), issues=issues)
        except ElementTree.ParseError, KeyError, ValueError, zipfile.BadZipFile:
            raise JpxCurrentListParseError("jpx_current_list_invalid") from None


def _shared_strings(body: bytes) -> tuple[str, ...]:
    if b"<!DOCTYPE" in body or b"<!ENTITY" in body:
        raise ValueError
    root = ElementTree.fromstring(body)
    return tuple("".join(node.itertext()) for node in root.findall("x:si", _NS))


def _rows(body: bytes, shared: tuple[str, ...]) -> list[list[str]]:
    root = ElementTree.fromstring(body)
    result: list[list[str]] = []
    for row in root.findall(".//x:sheetData/x:row", _NS):
        values = [""] * 10
        for cell in row.findall("x:c", _NS):
            match = _CELL.fullmatch(cell.attrib.get("r", ""))
            if match is None or int(match.group(2)) != int(row.attrib["r"]):
                raise ValueError
            index = ord(match.group(1)) - ord("A")
            value = cell.findtext("x:v", default="", namespaces=_NS)
            if cell.attrib.get("t") == "s":
                value = shared[int(value)]
            values[index] = value
        result.append(values)
    return result


def _issue(row: list[str]) -> JpxListedIssue:
    if len(row) != 10 or any(not value for value in row[:4]):
        raise ValueError
    raw_date, code, name, category, code33, name33, code17, name17, scale_code, scale_name = row
    snapshot = date(int(raw_date[:4]), int(raw_date[4:6]), int(raw_date[6:])).isoformat()
    segment = _ELIGIBLE_MARKETS.get(category)
    domestic = "（内国株式）" in category
    return JpxListedIssue(
        snapshot_on=snapshot,
        code=code,
        name=name,
        market_product_category=category,
        industry_33_code=code33 or None,
        industry_33_name=name33 or None,
        industry_17_code=code17 or None,
        industry_17_name=name17 or None,
        scale_code=scale_code or None,
        scale_name=scale_name or None,
        issuer_domesticity="domestic" if domestic else "unknown",
        security_class="ordinary_common_equity" if segment else "unknown",
        eligibility="eligible" if segment else "ineligible",
        market_segment=segment,
    )
