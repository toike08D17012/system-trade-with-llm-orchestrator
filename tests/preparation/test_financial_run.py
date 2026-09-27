"""Rendering tests for validated financial run dependencies and preserved gaps."""

import json
from datetime import UTC, datetime

import pytest

from stock_research_llm_orchestrator.preparation import financial_run
from stock_research_llm_orchestrator.preparation.financial_disclosure import FinancialManifest
from stock_research_llm_orchestrator.preparation.financial_mapping import METRICS
from stock_research_llm_orchestrator.preparation.financial_run import FinancialRunManifest
from stock_research_llm_orchestrator.preparation.task_input import create_human_selected_task


@pytest.mark.parametrize("count", [0, 2, 6])
def test_summary_preserves_partial_adoption_without_values(count: int, monkeypatch: pytest.MonkeyPatch) -> None:
    """Render already-validated states; real dependency replay is tested end to end separately."""
    checked = "2026-09-27T00:00:00+00:00"
    task = create_human_selected_task(
        "7203",
        1,
        mic="XTKS",
        task_id_factory=lambda: "task",
        accepted_at_factory=lambda: datetime(2026, 9, 27, tzinfo=UTC),
    )
    manifest = FinancialManifest(
        task_id="task",
        checked_at=checked,
        generation="generation",
        reasons=(
            "financial_mapping_unimplemented",
            "annual_periods_insufficient",
            "issuer_ir_unchecked",
            "price_fx_not_connected",
        ),
        filings=(),
        observed_list_dates=(),
        missing_list_dates=(),
        annual_periods=(),
        interim_periods=(),
        price_fx_status=None,
        hashes={},
    )
    source = {"task.json": task.model_dump_json().encode(), "manifest.json": manifest.model_dump_json().encode()}
    values = [
        {
            "metric": metric,
            "status": "accepted" if i < count else "unaccepted",
            "value": "987654321" if i < count else None,
            "currency": "JPY",
            "scope": "consolidated",
            "concept": None,
            "start_date": "2025-04-01",
            "end_date": "2026-03-31",
            "references": [],
            "reasons": [] if i < count else ["candidate_nil"],
        }
        for i, metric in enumerate(METRICS)
    ]
    monkeypatch.setattr(financial_run, "validate_acceptance", lambda *args: None)
    saved = financial_run.evaluate_financial_run(source, {}, {"values.json": json.dumps(values).encode()})
    result = FinancialRunManifest.model_validate_json(saved["manifest.json"])
    assert result.accepted_count == count and not result.analysis_ready and result.status == "pending"
    assert "annual_periods_insufficient" in result.reasons and "issuer_ir_unchecked" in result.reasons
    assert "financial_mapping_unimplemented" in result.historical_reasons
    assert "financial_mapping_unimplemented" not in result.reasons
    assert ("financial_mapping_partial" if count else "financial_mapping_unaccepted") in result.reasons
    assert result.price_fx is None and "price_fx_not_connected" in result.reasons
    assert b"987654321" not in b"".join(saved.values())
    assert all("value" not in metric for metric in json.loads(saved["manifest.json"])["metrics"])
