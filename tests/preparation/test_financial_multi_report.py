"""Cross-report coverage counts unique accepted facts without hiding conflicts."""

from datetime import date
from pathlib import Path

import pytest

from stock_research_llm_orchestrator.preparation.financial_acceptance import FinancialAcceptanceManifest, FinancialValue
from stock_research_llm_orchestrator.preparation.financial_comparative import AnnualPeriod, ComparativePeriodValues
from stock_research_llm_orchestrator.preparation.financial_mapping import METRICS
from stock_research_llm_orchestrator.preparation.financial_multi_report import (
    evaluate_multi_report,
    merge_coverage,
    prepare_multi_report,
)
from stock_research_llm_orchestrator.preparation.financial_multi_report_cli import main


PERIOD = AnnualPeriod(start_date=date(2023, 4, 1), end_date=date(2024, 3, 31))


def record(count: int, value: str = "100") -> ComparativePeriodValues:
    """Build a six-metric local record with partial adoption."""
    return ComparativePeriodValues(
        period=PERIOD,
        values=tuple(
            FinancialValue(
                metric=metric,
                status="accepted" if i < count else "unaccepted",
                value=value if i < count else None,
                concept=None,
                start_date=None if metric in {"assets", "equity"} else PERIOD.start_date,
                end_date=PERIOD.end_date,
                references=(),
                reasons=(),
            )
            for i, metric in enumerate(METRICS)
        ),
    )


def test_unique_coverage_and_equal_decimal_values() -> None:
    """Equal observations do not double-count or replace the required window."""
    missing = AnnualPeriod(start_date=date(2022, 4, 1), end_date=date(2023, 3, 31))
    result = merge_coverage((missing, PERIOD), ((record(3),), (record(6, "100.00"),)))
    assert result[0].status == "missing"
    assert result[1].status == "complete" and result[1].accepted_count == 6
    assert merge_coverage((PERIOD,), ((record(3),),))[0].status == "partial"
    assert merge_coverage((PERIOD,), ((record(0),),))[0].status == "missing"


@pytest.mark.parametrize("value", ["101", "NaN", "Infinity"])
def test_conflicting_or_nonfinite_values_rejected(value: str) -> None:
    """No silent selection between conflicting source values."""
    with pytest.raises(ValueError, match="multi_report_"):
        merge_coverage((PERIOD,), ((record(6),), (record(6, value),)))


def test_period_and_dependency_boundaries(tmp_path: Path) -> None:
    """Reject malformed coverage and publication before writing artifacts."""
    with pytest.raises(ValueError, match="outside_window"):
        merge_coverage((), ((record(6),),))
    with pytest.raises(ValueError, match="duplicate_period"):
        merge_coverage((PERIOD,), ((record(6), record(6)),))
    with pytest.raises(ValueError, match="dependency_set"):
        evaluate_multi_report({})
    with pytest.raises(ValueError, match="output_overlap"):
        prepare_multi_report({"financial": tmp_path}, tmp_path / "child")
    with pytest.raises(FileExistsError):
        prepare_multi_report({}, tmp_path)


def test_cli_requires_explicit_dependencies() -> None:
    """The CLI never follows manifest-supplied source paths."""
    with pytest.raises(SystemExit):
        main(["validate", "--input", "unused"])


def test_join_retains_source_limits_and_checks_compatibility(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate join behavior; raw-source replay is covered by acceptance tests."""
    import json

    from stock_research_llm_orchestrator.preparation import financial_multi_report as multi
    from stock_research_llm_orchestrator.preparation.financial_comparative import ComparativeManifest
    from stock_research_llm_orchestrator.preparation.financial_disclosure import FinancialManifest
    from stock_research_llm_orchestrator.preparation.financial_run import FinancialComparativeRunManifest

    base = FinancialComparativeRunManifest(
        task_id="primary",
        security_code="7203",
        checked_at="2026-09-26T00:00:00Z",
        accepted_count=0,
        metrics=(),
        price_fx=None,
        reasons=("gap",),
        historical_reasons=("gap",),
        inputs_sha256="0" * 64,
        annual_coverage=(),
        complete_annual_count=0,
        partial_annual_count=0,
        filing_annual_periods=(),
        limitations=("old_limit",),
    )
    calls = []

    def primary(*args: object) -> dict[str, bytes]:
        calls.append("primary")
        return {"manifest.json": base.model_dump_json().encode()}

    def additional(*args: object) -> None:
        calls.append("additional")

    monkeypatch.setattr(multi, "evaluate_financial_run", primary)
    monkeypatch.setattr(multi, "validate_comparative", additional)
    task = json.loads(
        Path(
            "tests/fixtures/contracts/detailed-analysis/v1/detailed-analysis-task/valid/human-selected.json"
        ).read_bytes()
    )
    d: dict[str, dict[str, bytes]] = {key: {} for key in multi.DEPENDENCIES}
    for prefix, year in (("", 2026), ("additional_", 2024)):
        task["task_id"] = f"source-{year}"
        checked = f"2026-09-{26 if year == 2026 else 27}T00:00:00Z"
        manifest = FinancialManifest(
            task_id=task["task_id"],
            checked_at=checked,
            generation="test",
            reasons=("source_gap",),
            filings=(),
            observed_list_dates=(),
            missing_list_dates=(),
            annual_periods=(),
            interim_periods=(),
            price_fx_status=None,
            hashes={},
        )
        d[prefix + "financial"] = {
            "task.json": json.dumps(task).encode(),
            "evaluation-policy.yaml": b"same",
            "manifest.json": manifest.model_dump_json().encode(),
        }
        d[prefix + "adoption"] = {
            "policy.json": Path(f"config/financial-mapping/7203-{year}-approved.json").read_bytes()
        }
        adoption_manifest = FinancialAcceptanceManifest(
            archive_sha256="0" * 64,
            source_manifest_sha256="0" * 64,
            review_sha256="0" * 64,
            inherited_reasons=(),
            hashes={},
            accepted_count=6,
        )
        d[prefix + "adoption"]["manifest.json"] = adoption_manifest.model_dump_json().encode()
        comp = ComparativeManifest(
            accepted_count=6,
            annual_coverage=(),
            complete_annual_count=1,
            partial_annual_count=0,
            hashes={},
            dependency_hashes={},
            limitations=(f"limit-{year}",),
        )
        d[prefix + "comparative"] = {
            "manifest.json": comp.model_dump_json().encode(),
            "policy.json": Path(f"config/financial-mapping/7203-{year}-comparative-approved.json").read_bytes(),
            "values.json": json.dumps([record(6).model_dump(mode="json")]).encode(),
        }
    files = multi.evaluate_multi_report(d)
    result = json.loads(files["manifest.json"])
    assert calls == ["primary", "additional"]
    assert result["accepted_count"] == 6 and result["complete_annual_count"] == 1
    assert {s["checked_at"] for s in result["sources"]} == {"2026-09-26T00:00:00Z", "2026-09-27T00:00:00Z"}
    assert {"limit-2024", "limit-2026", "old_limit"}.issubset(result["limitations"])
    assert b'"value"' not in files["manifest.json"]
    multi.validate_multi_report(files, d)
    with pytest.raises(ValueError, match="replay_mismatch"):
        multi.validate_multi_report({**files, "manifest.json": files["manifest.json"] + b" "}, d)
    for field, replacement, error in (
        ("evaluation-policy.yaml", b"different", "evaluation_policy"),
        (
            "task.json",
            json.dumps({**task, "security": {"security_code": "9999", "mic": "XTKS"}}).encode(),
            "task_scope",
        ),
    ):
        altered = {**d, "additional_financial": {**d["additional_financial"], field: replacement}}
        with pytest.raises(ValueError, match=error):
            multi.evaluate_multi_report(altered)
    policy = json.loads(d["additional_adoption"]["policy.json"])
    policy["edinet_code"] = "E00001"
    with pytest.raises(ValueError, match="issuer_mismatch"):
        multi.evaluate_multi_report({**d, "additional_adoption": {"policy.json": json.dumps(policy).encode()}})
