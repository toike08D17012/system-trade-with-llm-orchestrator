"""Bounded, deterministic intake of Toyota's English interim statements."""

import json
import re
import subprocess
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from stock_research_llm_orchestrator.contracts.base import Sha256Hex, StrictContractModel
from stock_research_llm_orchestrator.preparation.financial_mapping import METRICS, Metric


MAX_PDF_BYTES = 5_000_000
MAX_PAGES = 64
MAX_TEXT_BYTES = 2_000_000
MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
MONTH_PATTERN = "|".join(r"\s*".join(month) for month in MONTHS.split("|"))
DATE = re.compile(rf"\b({MONTH_PATTERN})\s+(\d(?:\s*\d)?)\s*,?\s+(\d(?:\s*\d){{3}})\b")
MONEY = r"(?:\(\d+(?:,\d{3})*\)|-?\d+(?:,\d{3})*|[—–-])"
LABELS: dict[Metric, tuple[str, str]] = {
    "revenue": ("income", "Total sales revenues"),
    "operating_profit": ("income", "Operating income"),
    "parent_profit": ("income", "Toyota Motor Corporation"),
    "assets": ("position", "Total assets"),
    "equity": ("position", "Total shareholders' equity"),
    "operating_cash_flow": ("cash", "Net cash provided by (used in) operating activities"),
}


class InterimDocument(StrictContractModel):
    """One selected statement and its source-native cumulative period."""

    key: str
    url: str
    fiscal_year: int
    quarter: int = Field(ge=1, le=3)
    start_date: date
    end_date: date
    published_on: date
    sha256: Sha256Hex

    @model_validator(mode="after")
    def validate_period(self) -> InterimDocument:
        """Tie each official document key to its fiscal cumulative period."""
        key = f"{self.fiscal_year}_{self.quarter}q"
        if (
            self.key != key
            or self.url != f"https://global.toyota/pages/global_toyota/ir/financial-results/{key}_summary_en.pdf"
            or self.start_date != date(self.fiscal_year - 1, 4, 1)
            or self.end_date != date(self.fiscal_year - 1, self.quarter * 3 + 3, 31 if self.quarter == 3 else 30)
            or self.published_on < self.end_date
        ):
            raise ValueError("interim_document_period_invalid")
        return self


class IrMetric(StrictContractModel):
    """Keep normalized financial values local, with table and column provenance."""

    metric: Metric
    status: Literal["accepted", "unaccepted"]
    value: str | None
    currency: Literal["JPY"] = "JPY"
    scope: Literal["consolidated"] = "consolidated"
    source_unit: Literal["JPY_millions"] = "JPY_millions"
    start_date: date | None
    end_date: date
    period_basis: Literal["instant", "fiscal_year_to_date"]
    references: tuple[tuple[int, int, int], ...]
    reasons: tuple[str, ...]


def _dates(text: str) -> tuple[date, ...]:
    return tuple(
        datetime.strptime(" ".join(re.sub(r"\s", "", group) for group in m.groups()), "%B %d %Y").date()
        for m in DATE.finditer(text)
    )


def extract_pages(body: bytes) -> tuple[str, ...]:
    """Never emit PDF contents or parser diagnostics to the caller's console."""
    if len(body) > MAX_PDF_BYTES or not body.startswith(b"%PDF-"):
        raise ValueError("ir_pdf_size_or_signature_invalid")
    try:
        result = subprocess.run(
            [sys.executable, str(Path(__file__).with_name("_pdf_extract.py")), "--extract"],
            input=body,
            capture_output=True,
            timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise ValueError("ir_pdf_extraction_timeout") from None
    if result.returncode or len(result.stdout) > MAX_TEXT_BYTES * 6:
        raise ValueError("ir_pdf_extraction_failed")
    return tuple(json.loads(result.stdout))


def parse_pages(pages: tuple[str, ...], document: InterimDocument) -> tuple[IrMetric, ...]:
    """Select only current columns of consolidated cumulative/instant tables."""
    front = " ".join(pages[0].split()) if pages else ""
    if (
        re.search(rf"\bFY{document.fiscal_year}\b", front) is None
        or "TOYOTA MOTOR CORPORATION" not in front.upper()
        or document.published_on not in _dates(" ".join(pages[:2]))
    ):
        raise ValueError("ir_pdf_document_identity_mismatch")
    matches: dict[Metric, list[tuple[str | None, tuple[int, int, int]]]] = {metric: [] for metric in METRICS}
    section = ""
    prior_section = ""
    for page_number, text in enumerate(pages, 1):
        lines = text.splitlines()
        normalized = " ".join(text.lower().split())
        if "consolidated statement of financial position" in normalized:
            section = "position"
        elif "consolidated statement of cash flows" in normalized:
            section = "cash"
        elif "consolidated statement of income" in normalized:
            section = "income"
        elif prior_section == "position" and "total shareholders' equity" in normalized:
            section = "position"
        else:
            section = ""
        prior_section = section
        if not section or re.search(r"\byen\s+in\s+m\s*i\s*l\s*l\s*i\s*o\s*n\s*s\b", normalized) is None:
            continue
        if section != "position":
            terms = {
                1: ("first quarter ended", "three-month", "three months"),
                2: ("six-month", "six months", "first half ended"),
                3: ("nine-month", "nine months"),
            }[document.quarter]
            if not any(term in normalized for term in terms):
                continue
        header_dates = [ds for line in lines[:24] if len(ds := _dates(line)) == 2]
        expected_prior = (
            date(document.fiscal_year - 1, 3, 31)
            if section == "position"
            else document.end_date.replace(year=document.end_date.year - 1)
        )
        valid_headers = [ds for ds in header_dates if set(ds) == {expected_prior, document.end_date}]
        if len(valid_headers) != 1:
            continue
        column = valid_headers[0].index(document.end_date)
        for row, line in enumerate(lines):
            for metric, (wanted, label) in LABELS.items():
                if wanted != section:
                    continue
                if (
                    metric == "parent_profit"
                    and "net income attributable to" not in " ".join(lines[max(0, row - 2) : row]).lower()
                ):
                    continue
                match = re.fullmatch(rf"\s*{re.escape(label)}\s+({MONEY})\s+({MONEY})\s*", line, re.I)
                if match is None:
                    continue
                value = match.group(column + 1)
                normalized_value = (
                    None
                    if value in {"-", "—", "–"}
                    else str(int(Decimal(value.replace(",", "").replace("(", "-").replace(")", "")) * 1_000_000))
                )
                matches[metric].append((normalized_value, (page_number, row + 1, column + 1)))
    result = []
    for metric, observations in matches.items():
        values = {v for v, _ in observations}
        accepted = len(values) == 1 and None not in values
        instant = metric in {"assets", "equity"}
        result.append(
            IrMetric(
                metric=metric,
                status="accepted" if accepted else "unaccepted",
                value=next(iter(values)) if accepted else None,
                start_date=None if instant else document.start_date,
                end_date=document.end_date,
                period_basis="instant" if instant else "fiscal_year_to_date",
                references=tuple(ref for _, ref in observations),
                reasons=() if accepted else ("conflicting_rows" if len(values) > 1 else "missing_or_unsupported_row",),
            )
        )
    return tuple(result)
