"""Replay a private Toyota monthly sales workbook and extracted observation."""

import argparse
import json
from collections.abc import Mapping
from datetime import date
from decimal import InvalidOperation
from hashlib import sha256
from pathlib import Path

from stock_research_llm_orchestrator.preparation.market_revalidation import _timestamp
from stock_research_llm_orchestrator.preparation.storage import publish_preparation, read_bundle
from stock_research_llm_orchestrator.sources.toyota_ir.monthly_sales import MonthlySalesObservation, parse_monthly_sales


def _url(month: date) -> str:
    return (
        "https://global.toyota/pages/global_toyota/company/profile/production-sales-figures/"
        f"production_sales_figures_{month:%Y%m}.xlsx"
    )


def _record(observation: MonthlySalesObservation) -> dict[str, str | int]:
    return {
        "month": observation.month.isoformat(),
        "worldwide_sales": observation.worldwide_sales,
        "worldwide_yoy": str(observation.worldwide_yoy),
        "hev_sales": observation.hev_sales,
        "hev_yoy": str(observation.hev_yoy),
        "bev_sales": observation.bev_sales,
        "bev_yoy": str(observation.bev_yoy),
        "electrified_sales": observation.electrified_sales,
        "electrified_yoy": str(observation.electrified_yoy),
    }


def validate_monthly_bundle(files: Mapping[str, bytes]) -> None:
    """Reject any source, receipt or derived-value change."""
    if set(files) != {"workbook.xlsx", "receipt.json", "observation.json"}:
        raise ValueError("toyota_monthly_bundle_inventory_invalid")
    receipt = json.loads(files["receipt.json"])
    record = json.loads(files["observation.json"])
    month = date.fromisoformat(record["month"])
    if (
        record["kind"] != "toyota-monthly-vehicle-sales"
        or record["evidence_status"] != "research-only"
        or receipt["url"] != _url(month)
        or receipt["status_code"] != 200
        or receipt["content_type"] != "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        or receipt["sha256"] != sha256(files["workbook.xlsx"]).hexdigest()
        or receipt["byte_count"] != len(files["workbook.xlsx"])
        or _timestamp(receipt["requested_at"]) > _timestamp(receipt["received_at"])
        or record["analysis_ready"] is not False
    ):
        raise ValueError("toyota_monthly_bundle_binding_invalid")
    observation = parse_monthly_sales(files["workbook.xlsx"], month)
    if record["observation"] != _record(observation):
        raise ValueError("toyota_monthly_bundle_values_invalid")


def build_monthly_bundle(workbook: bytes, receipt_bytes: bytes, month: date) -> dict[str, bytes]:
    """Save exact official source bytes and a reproducible monthly observation."""
    observation = parse_monthly_sales(workbook, month)
    record: dict[str, object] = {
        "kind": "toyota-monthly-vehicle-sales",
        "evidence_status": "research-only",
        "analysis_ready": False,
        "month": month.isoformat(),
    }
    record["observation"] = _record(observation)
    files = {
        "workbook.xlsx": workbook,
        "receipt.json": receipt_bytes,
        "observation.json": json.dumps(record, indent=2).encode(),
    }
    validate_monthly_bundle(files)
    return files


def main(argv: list[str] | None = None) -> int:
    """Build or offline-validate one private month bundle."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("--workbook", type=Path, required=True)
    build.add_argument("--receipt", type=Path, required=True)
    build.add_argument("--month", type=date.fromisoformat, required=True)
    build.add_argument("--runs", type=Path, required=True)
    build.add_argument("--destination", required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("--bundle", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            publish_preparation(
                args.runs,
                args.destination,
                build_monthly_bundle(args.workbook.read_bytes(), args.receipt.read_bytes(), args.month),
                validator=validate_monthly_bundle,
            )
            bundle = args.runs / args.destination
        else:
            bundle = args.bundle
        files = read_bundle(bundle)
        validate_monthly_bundle(files)
        observation = json.loads(files["observation.json"])["observation"]
        print(f"toyota_monthly_sales: valid; month={observation['month']}; analysis_ready=false")
    except (ValueError, OSError, RuntimeError, KeyError, TypeError, InvalidOperation) as error:
        print(f"toyota_monthly_sales_failed: {type(error).__name__}: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
