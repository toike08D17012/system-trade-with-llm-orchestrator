"""Read selected monthly Toyota vehicle sales from the official XLSX workbook."""

import io
from calendar import month_abbr, month_name
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile


_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


@dataclass(frozen=True)
class MonthlySalesObservation:
    """Toyota plus Lexus worldwide monthly sales and powertrain composition."""

    month: date
    worldwide_sales: int
    worldwide_yoy: Decimal
    hev_sales: int
    hev_yoy: Decimal
    bev_sales: int
    bev_yoy: Decimal
    electrified_sales: int
    electrified_yoy: Decimal


def _cells(source: bytes, shared: list[str]) -> dict[str, str]:
    root = ElementTree.fromstring(source)
    cells: dict[str, str] = {}
    for cell in root.findall(".//m:sheetData/m:row/m:c", _NS):
        address = cell.get("r")
        value = cell.find("m:v", _NS)
        if address is None or value is None or value.text is None:
            continue
        if address in cells:
            raise ValueError("toyota_monthly_sales_duplicate_cell")
        if cell.get("t") == "s":
            index = int(value.text)
            cells[address] = shared[index]
        else:
            cells[address] = value.text
    return cells


def _column(cells: dict[str, str], month: date) -> str:
    labels = {f"{month_abbr[month.month]}{month.year}", f"{month_name[month.month]}{month.year}"}
    if month.month == 9:
        labels.add(f"Sept{month.year}")
    matches = [
        address[:-1]
        for address, value in cells.items()
        if address.endswith("3") and value.replace(".", "").replace(" ", "") in labels
    ]
    if len(matches) != 1:
        raise ValueError("toyota_monthly_sales_month_missing")
    return matches[0]


def _integer(cells: dict[str, str], address: str) -> int:
    try:
        number = Decimal(cells[address])
    except KeyError, InvalidOperation:
        raise ValueError("toyota_monthly_sales_value_invalid") from None
    if not number.is_finite() or number < 0 or number != int(number):
        raise ValueError("toyota_monthly_sales_value_invalid")
    return int(number)


def _ratio(cells: dict[str, str], address: str) -> Decimal:
    try:
        number = Decimal(cells[address])
    except KeyError, InvalidOperation:
        raise ValueError("toyota_monthly_sales_ratio_invalid") from None
    if not number.is_finite() or number < -1:
        raise ValueError("toyota_monthly_sales_ratio_invalid")
    return number


def parse_monthly_sales(body: bytes, month: date) -> MonthlySalesObservation:
    """Parse official worldwide sales and verify electrified subtotals."""
    if month.day != 1 or not 0 < len(body) <= 5_000_000:
        raise ValueError("toyota_monthly_sales_input_invalid")
    try:
        with ZipFile(io.BytesIO(body)) as archive:
            names = archive.namelist()
            if (
                len(names) > 100
                or len(names) != len(set(names))
                or sum(item.file_size for item in archive.infolist()) > 10_000_000
                or any(item.file_size > 3_000_000 for item in archive.infolist())
            ):
                raise ValueError("toyota_monthly_sales_archive_invalid")
            strings = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
            shared = [
                "".join(node.text or "" for node in entry.findall(".//m:t", _NS))
                for entry in strings.findall("m:si", _NS)
            ]
            workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
            sheets = {sheet.get("name") for sheet in workbook.findall(".//m:sheet", _NS)}
            if "販売_Sales" not in sheets or "電動車_Electrified vehicle sales" not in sheets:
                raise ValueError("toyota_monthly_sales_sheets_invalid")
            sales = _cells(archive.read("xl/worksheets/sheet2.xml"), shared)
            electric = _cells(archive.read("xl/worksheets/sheet8.xml"), shared)
    except BadZipFile, KeyError, ElementTree.ParseError, IndexError:
        raise ValueError("toyota_monthly_sales_archive_invalid") from None
    if (
        "Toyota (incl. Lexus)" not in sales.get("B4", "")
        or "Worldwide sales" not in sales.get("C5", "")
        or "Electrified vehicle sales" not in electric.get("B1", "")
        or [electric.get(f"B{row}", "").strip() for row in (4, 11, 18, 25, 32)]
        != ["HEV", "MHEV", "PHEV", "FCEV", "BEV"]
    ):
        raise ValueError("toyota_monthly_sales_labels_invalid")
    sales_col, electric_col = _column(sales, month), _column(electric, month)
    worldwide = _integer(sales, f"{sales_col}5")
    powered = [_integer(electric, f"{electric_col}{row}") for row in (5, 12, 19, 26, 33)]
    total = _integer(electric, f"{electric_col}40")
    if sum(powered) != total or total > worldwide:
        raise ValueError("toyota_monthly_sales_subtotal_invalid")
    return MonthlySalesObservation(
        month=month,
        worldwide_sales=worldwide,
        worldwide_yoy=_ratio(sales, f"{sales_col}6"),
        hev_sales=powered[0],
        hev_yoy=_ratio(electric, f"{electric_col}6"),
        bev_sales=powered[4],
        bev_yoy=_ratio(electric, f"{electric_col}34"),
        electrified_sales=total,
        electrified_yoy=_ratio(electric, f"{electric_col}41"),
    )
