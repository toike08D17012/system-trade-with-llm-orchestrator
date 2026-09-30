"""Toyota monthly vehicle sales workbook parsing tests."""

import io
import json
from datetime import date
from decimal import Decimal
from hashlib import sha256
from zipfile import ZipFile

import pytest

from stock_research_llm_orchestrator.preparation.toyota_monthly_sales import (
    build_monthly_bundle,
    validate_monthly_bundle,
)
from stock_research_llm_orchestrator.sources.toyota_ir.monthly_sales import parse_monthly_sales


def _workbook(*, electric_total: int = 435602) -> bytes:
    """Make a tiny official-shaped workbook with a configurable subtotal."""
    strings = [
        "Toyota (incl. Lexus)",
        "Worldwide sales",
        "Toyota (incl. Lexus) Electrified vehicle sales",
        "HEV",
        "MHEV",
        "PHEV",
        "FCEV",
        "BEV",
        "Aug.2026",
    ]

    def sheet(cells: list[tuple[str, int | float | str]]) -> str:
        parts = []
        for address, value in cells:
            if isinstance(value, str):
                parts.append(f'<c r="{address}" t="s"><v>{strings.index(value)}</v></c>')
            else:
                parts.append(f'<c r="{address}"><v>{value}</v></c>')
        return (
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row>'
            + "".join(parts)
            + "</row></sheetData></worksheet>"
        )

    sales = sheet(
        [
            ("B4", strings[0]),
            ("C5", strings[1]),
            ("AR3", strings[8]),
            ("AR5", 790743),
            ("AR6", -0.06416849),
        ]
    )
    electric = sheet(
        [
            ("B1", strings[2]),
            ("B4", strings[3]),
            ("B11", strings[4]),
            ("B18", strings[5]),
            ("B25", strings[6]),
            ("B32", strings[7]),
            ("AS3", strings[8]),
            ("AS5", 362212),
            ("AS6", 0.07153485),
            ("AS12", 13921),
            ("AS19", 21898),
            ("AS26", 85),
            ("AS33", 37486),
            ("AS34", 1.19781894),
            ("AS40", electric_total),
            ("AS41", 0.13705999),
        ]
    )
    output = io.BytesIO()
    with ZipFile(output, "w") as archive:
        archive.writestr(
            "xl/workbook.xml",
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheets>'
            '<sheet name="販売_Sales"/><sheet name="電動車_Electrified vehicle sales"/>'
            "</sheets></workbook>",
        )
        archive.writestr(
            "xl/sharedStrings.xml",
            '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            + "".join(f"<si><t>{value}</t></si>" for value in strings)
            + "</sst>",
        )
        archive.writestr("xl/worksheets/sheet2.xml", sales)
        archive.writestr("xl/worksheets/sheet8.xml", electric)
    return output.getvalue()


def test_monthly_sales_extracts_worldwide_and_powertrain_values() -> None:
    """The report period, totals and growth rates are read from named cells."""
    observation = parse_monthly_sales(_workbook(), date(2026, 8, 1))
    assert observation.worldwide_sales == 790743
    assert observation.hev_sales == 362212
    assert observation.bev_sales == 37486
    assert observation.bev_yoy == Decimal("1.19781894")


def test_monthly_sales_rejects_inconsistent_subtotal_and_invalid_archive() -> None:
    """Corrupt or inconsistent workbooks cannot become business evidence."""
    with pytest.raises(ValueError, match="subtotal_invalid"):
        parse_monthly_sales(_workbook(electric_total=1), date(2026, 8, 1))
    with pytest.raises(ValueError, match="archive_invalid"):
        parse_monthly_sales(b"not a workbook", date(2026, 8, 1))


def test_monthly_sales_bundle_replays_download_and_derived_values() -> None:
    """A changed workbook or observation invalidates private research output."""
    workbook = _workbook()
    receipt = json.dumps(
        {
            "url": "https://global.toyota/pages/global_toyota/company/profile/production-sales-figures/"
            "production_sales_figures_202608.xlsx",
            "requested_at": "2026-09-30T15:00:00+00:00",
            "received_at": "2026-09-30T15:00:01+00:00",
            "sha256": sha256(workbook).hexdigest(),
            "byte_count": len(workbook),
            "status_code": 200,
            "content_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        }
    ).encode()
    files = build_monthly_bundle(workbook, receipt, date(2026, 8, 1))
    validate_monthly_bundle(files)
    changed = dict(files)
    changed["workbook.xlsx"] += b" "
    with pytest.raises(ValueError, match="binding_invalid"):
        validate_monthly_bundle(changed)
    changed = dict(files)
    observation = json.loads(changed["observation.json"])
    observation["observation"]["bev_sales"] = 1
    changed["observation.json"] = json.dumps(observation).encode()
    with pytest.raises(ValueError, match="values_invalid"):
        validate_monthly_bundle(changed)
