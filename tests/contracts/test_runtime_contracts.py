"""Tests for Agent execution, task runtime, manifest, and logical operations."""

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.agent_execution import (
    AgentExecutionV1,
    validate_agent_execution_history,
)
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.operations import (
    OperationRequestV1,
    OperationResultV1,
)
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.runtime import (
    ExecutionManifestV1,
    TaskStateTransitionV1,
)
from stock_research_llm_orchestrator.contracts.validation.wrapper import InputFormat, validate_text


FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "contracts" / "detailed-analysis" / "v1"


def _payload(artifact: str, filename: str) -> dict[str, object]:
    path = FIXTURE_ROOT / artifact / "valid" / filename
    return json.loads(path.read_text(encoding="utf-8"))


def _reference(schema_id: str, artifact_id: str) -> dict[str, object]:
    return {
        "artifact_type": schema_id.removeprefix("detailed-analysis.").replace("-", "_"),
        "artifact_id": artifact_id,
        "schema_id": schema_id,
        "schema_version": 1,
        "sha256": "9" * 64,
    }


@pytest.mark.parametrize(
    ("artifact", "filename", "model"),
    [
        ("agent-execution", "succeeded-worker.json", AgentExecutionV1),
        ("execution-manifest", "running.json", ExecutionManifestV1),
        ("operation-request", "status.json", OperationRequestV1),
        ("operation-result", "status.json", OperationResultV1),
    ],
)
def test_valid_runtime_fixtures(artifact: str, filename: str, model: type[object]) -> None:
    """Dispatch each public runtime artifact through the common validator."""
    path = FIXTURE_ROOT / artifact / "valid" / filename

    result = validate_text(path.read_text(encoding="utf-8"), InputFormat.JSON)

    assert isinstance(result, model)


def test_unretrieved_agent_usage_cannot_be_zero() -> None:
    """Keep unmeasured provider usage explicit instead of inventing zero."""
    payload = _payload("agent-execution", "succeeded-worker.json")
    payload["usage"][0]["value"] = 0  # type: ignore[index]

    with pytest.raises(ValidationError, match="unavailable Agent usage must have a null value"):
        AgentExecutionV1.model_validate(payload)


def test_retrieved_agent_usage_can_be_zero() -> None:
    """Preserve a provider-reported or derived zero when its meaning is confirmed."""
    payload = _payload("agent-execution", "succeeded-worker.json")
    payload["usage"][0].update(  # type: ignore[index]
        {
            "availability": "retrieved",
            "origin": "provider_reported",
            "scope": "run",
            "meaning_confirmed": True,
            "value": 0,
        }
    )

    result = AgentExecutionV1.model_validate(payload)

    assert result.usage[0].value == 0


def test_meaning_unconfirmed_agent_usage_cannot_be_numeric() -> None:
    """Keep observed but semantically ambiguous usage out of common numeric fields."""
    payload = _payload("agent-execution", "succeeded-worker.json")
    payload["usage"][0].update(  # type: ignore[index]
        {
            "availability": "retrieved",
            "origin": "provider_reported",
            "scope": "unknown",
            "meaning_confirmed": False,
            "value": 1,
        }
    )

    with pytest.raises(ValidationError, match="meaning-unconfirmed Agent usage must not contain a numeric value"):
        AgentExecutionV1.model_validate(payload)


def test_logical_session_allows_only_one_active_run() -> None:
    """Reject concurrent Agent runs inside one logical session."""
    first_payload = _payload("agent-execution", "succeeded-worker.json")
    first_payload.update(
        {
            "process_state": "running",
            "ended_at": None,
            "exit_code": None,
            "output_reference": None,
            "active_run": True,
        }
    )
    second_payload = copy.deepcopy(first_payload)
    second_payload["artifact_id"] = "agent-execution-2"
    second_payload["agent_run_id"] = "agent-run-2"
    first = AgentExecutionV1.model_validate(first_payload)
    second = AgentExecutionV1.model_validate(second_payload)

    with pytest.raises(ValueError, match="at most one active run"):
        validate_agent_execution_history((first, second))


def test_resume_cannot_change_logical_session_settings() -> None:
    """Keep model, role, permissions, policy, and output schema fixed on resume."""
    first = AgentExecutionV1.model_validate(_payload("agent-execution", "succeeded-worker.json"))
    resume_payload = _payload("agent-execution", "succeeded-worker.json")
    resume_payload["artifact_id"] = "agent-execution-2"
    resume_payload["agent_run_id"] = "agent-run-2"
    resume_payload["parent_agent_run_id"] = "agent-run-1"
    resume_payload["session_mode"] = "resume"
    resume_payload["settings"]["settings_sha256"] = "8" * 64  # type: ignore[index]
    resumed = AgentExecutionV1.model_validate(resume_payload)

    with pytest.raises(ValueError, match="settings must not change"):
        validate_agent_execution_history((first, resumed))


def test_task_state_rejects_skipping_interrupting() -> None:
    """Require running tasks to enter interrupting before suspended."""
    payload = {
        "transition_id": "transition-1",
        "operation_id": "operation-1",
        "from_state": "running",
        "to_state": "suspended",
        "occurred_at": "2026-09-12T12:00:00+09:00",
        "reason_code": "unsafe-skip",
        "reason": "Attempted direct suspension.",
    }

    with pytest.raises(ValidationError, match="transition is not allowed"):
        TaskStateTransitionV1.model_validate(payload)


def test_analysis_completed_rejects_review_blockers() -> None:
    """Block completion while a pending classification remains."""
    payload = _payload("execution-manifest", "running.json")
    payload["state_transitions"].append(  # type: ignore[union-attr]
        {
            "transition_id": "transition-2",
            "operation_id": "operation-2",
            "from_state": "running",
            "to_state": "analysis_completed",
            "occurred_at": "2026-09-12T13:00:00+09:00",
            "reason_code": "analysis-complete",
            "reason": "All validated outputs were produced.",
        }
    )
    payload["current_state"] = "analysis_completed"
    payload["primary_review_passed"] = True
    payload["classification_pending_present"] = True
    payload["final_analysis_result_reference"] = _reference("detailed-analysis.final-analysis-result", "final-result-1")

    with pytest.raises(ValidationError, match="blocked by unresolved review"):
        ExecutionManifestV1.model_validate(payload)


def test_manifest_requires_exact_instruction_application_schema() -> None:
    """Track applied instructions through their exact public contract."""
    payload = _payload("execution-manifest", "running.json")
    payload["instruction_application_references"] = [
        _reference("detailed-analysis.agent-execution", "wrong-instruction-reference")
    ]

    with pytest.raises(ValidationError, match="exact public schemas"):
        ExecutionManifestV1.model_validate(payload)


def test_mutating_operation_requires_expected_state_and_manifest() -> None:
    """Prevent a stale interrupt request from mutating an unknown task state."""
    payload = _payload("operation-request", "status.json")
    payload["operation_type"] = "interrupt"
    payload["manifest_reference"] = None

    with pytest.raises(ValidationError, match="require manifest and expected state"):
        OperationRequestV1.model_validate(payload)


def test_failed_operation_requires_nonzero_exit_and_safe_error() -> None:
    """Keep logical operation failure fields internally consistent."""
    payload = _payload("operation-result", "status.json")
    payload["status"] = "failed"

    with pytest.raises(ValidationError, match="require an error"):
        OperationResultV1.model_validate(payload)
