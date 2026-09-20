"""Integration tests for constructing and publishing one task."""

from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.task import DetailedAnalysisTaskV1
from stock_research_llm_orchestrator.preparation.storage import publish_task_preparation
from stock_research_llm_orchestrator.preparation.task_input import create_human_selected_task


def test_publish_task_preparation_keeps_validated_serialized_bytes(tmp_path: Path) -> None:
    """Store one task using the same bytes represented by the receipt hash."""
    task = create_human_selected_task(
        "7203",
        1,
        mic="XTKS",
        task_id_factory=lambda: "task-fixed-001",
        accepted_at_factory=lambda: datetime(2026, 9, 20, 9, 30, tzinfo=UTC),
    )

    receipt = publish_task_preparation(tmp_path, "prep-001", task)

    stored_bytes = (tmp_path / "prep-001" / "task.json").read_bytes()
    assert DetailedAnalysisTaskV1.model_validate_json(stored_bytes) == task
    assert receipt.files[0].relative_path == "task.json"
    assert receipt.files[0].sha256 == sha256(stored_bytes).hexdigest()
