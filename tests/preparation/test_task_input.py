"""Tests for single-security task construction."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.task import AnalysisHorizon, TaskOrigin
from stock_research_llm_orchestrator.preparation.task_input import create_human_selected_task


FIXED_TIME = datetime(2026, 9, 20, 9, 30, tzinfo=UTC)


def test_create_human_selected_task_uses_approved_scope() -> None:
    """Build one immutable task with fixed system-owned metadata."""
    task = create_human_selected_task(
        "012A",
        3,
        mic="XTKS",
        task_id_factory=lambda: "task-fixed-001",
        accepted_at_factory=lambda: FIXED_TIME,
    )

    assert task.task_id == "task-fixed-001"
    assert task.task_accepted_at == "2026-09-20T09:30:00+00:00"
    assert task.origin is TaskOrigin.HUMAN_SELECTED
    assert task.parent_task_id is None
    assert task.security.security_code == "012A"
    assert task.security.mic == task.market.mic == "XTKS"
    assert task.market.timezone == "Asia/Tokyo"
    assert task.analysis_horizons == (AnalysisHorizon.MEDIUM_TERM, AnalysisHorizon.LONG_TERM)
    assert not any(task.constraints.model_dump().values())


@pytest.mark.parametrize("security_code", ["", "7203,6758", "7203-T", "銘柄"])
def test_create_human_selected_task_rejects_invalid_single_code(security_code: str) -> None:
    """Reject empty, list-like, and out-of-contract security codes."""
    with pytest.raises(ValidationError):
        create_human_selected_task(
            security_code,
            1,
            mic="XTKS",
            task_id_factory=lambda: "task-fixed-001",
            accepted_at_factory=lambda: FIXED_TIME,
        )


def test_create_human_selected_task_rejects_invalid_system_metadata() -> None:
    """Reject malformed identifiers, policy versions, and offset-free times."""
    with pytest.raises(ValidationError):
        create_human_selected_task(
            "7203",
            0,
            mic="XTKS",
            task_id_factory=lambda: "task-fixed-001",
            accepted_at_factory=lambda: FIXED_TIME,
        )
    with pytest.raises(ValidationError):
        create_human_selected_task(
            "7203",
            1,
            mic="XTKS",
            task_id_factory=lambda: "invalid task id",
            accepted_at_factory=lambda: FIXED_TIME,
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        create_human_selected_task(
            "7203",
            1,
            mic="XTKS",
            task_id_factory=lambda: "task-fixed-001",
            accepted_at_factory=lambda: datetime(2026, 9, 20, 9, 30),
        )


def test_create_human_selected_task_rejects_unsupported_market() -> None:
    """Reject a market outside the approved initial scope."""
    with pytest.raises(ValueError, match="only the XTKS"):
        create_human_selected_task(
            "7203",
            1,
            mic="XNAS",
            task_id_factory=lambda: "task-fixed-001",
            accepted_at_factory=lambda: FIXED_TIME,
        )
