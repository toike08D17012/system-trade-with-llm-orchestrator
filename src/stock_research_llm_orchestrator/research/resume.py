"""Validate retained acquisition inputs before restarting independent research roles."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.acquisition_analysis import (
    AcquisitionRequest,
    AcquisitionResult,
)
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.agent_configuration import AgentConfigurationV1
from stock_research_llm_orchestrator.preparation.research_acquisition import validate_acquisition
from stock_research_llm_orchestrator.preparation.storage import read_bundle
from stock_research_llm_orchestrator.research.baseline import validate_baseline
from stock_research_llm_orchestrator.research.input import validate_input


@dataclass(frozen=True)
class RetainedTurn:
    """Actual completed provider output and the exact request that produced it."""

    request: bytes
    response: bytes
    trace: bytes
    source: Path
    captured_at: str


def load_resume(root: Path) -> tuple[dict[str, bytes], AgentConfigurationV1, dict[str, bytes]]:
    """Load exact saved inputs and baseline; do not trust incomplete provider responses.

    Roles restart as fresh provider invocations, with retained acquisition times and
    originals. This does not resume an opaque provider conversation or certify freshness.
    """
    inputs = read_bundle(root / "input")
    validate_input(inputs)
    config = AgentConfigurationV1.model_validate_json((root / "agent-configuration.json").read_bytes())
    baseline = read_bundle(root / "baseline")
    validate_baseline(baseline, inputs=inputs, config=config)
    return inputs, config, baseline


def load_retained_acquisitions(
    root: Path,
) -> tuple[tuple[AcquisitionRequest, AcquisitionResult, dict[str, bytes]], ...]:
    """Retain only complete, validated originals for honest cache reuse after interruption."""
    retained: list[tuple[AcquisitionRequest, AcquisitionResult, dict[str, bytes]]] = []
    baseline = root / "baseline"
    for path in sorted((baseline / "requests").glob("*.json"), key=lambda value: int(value.stem)):
        request = AcquisitionRequest.model_validate_json(path.read_bytes())
        result = AcquisitionResult.model_validate_json((baseline / "results" / path.name).read_bytes())
        files = _payload(baseline / "acquisitions" / path.stem)
        validate_acquisition(request, result, files)
        if result.status == "acquired":
            retained.append((request, result, files))
    for path in sorted((root / "acquisitions").glob("[0-9]*"), key=lambda value: int(value.name)):
        if not (path / "result.json").is_file():
            continue
        request = AcquisitionRequest.model_validate_json((path / "request.json").read_bytes())
        result = AcquisitionResult.model_validate_json((path / "result.json").read_bytes())
        files = _payload(path / "files")
        validate_acquisition(request, result, files)
        if result.status == "acquired":
            retained.append((request, result, files))
    return tuple(retained)


def _payload(path: Path) -> dict[str, bytes]:
    """A fileless unavailable result needs no directory; its references are still validated."""
    return read_bundle(path) if path.exists() else {}


def load_completed_turns(
    root: Path, *, include_pending_snapshots: bool = False
) -> tuple[dict[str, RetainedTurn], dict[str, str]]:
    """Recover completed calls, preserving snapshot times and real response-capture times.

    Exact artifact creation times are recovered from downstream inputs when present.
    Otherwise, an unpublished analysis is reconstructed at its recorded provider
    response capture time, not a fabricated historical serialization timestamp.
    """
    turns: dict[str, RetainedTurn] = {}
    stamps: dict[str, str] = {}
    exact_artifact_stamps: dict[str, str] = {}
    for directory in sorted(root.glob("[0-9][0-9][0-9][0-9]-*")):
        if include_pending_snapshots and (directory / "request.json").is_file():
            pending_record = json.loads((directory / "request.json").read_bytes())
            pending_snapshot = pending_record["data"].get("evidence_snapshot")
            if pending_snapshot is not None:
                pending_key = pending_snapshot["reference"]["artifact_id"]
                stamps["snapshot-" + pending_key] = pending_snapshot["created_at"]
        if (directory / "failure.json").exists() or not (directory / "response.json").is_file():
            continue
        request = (directory / "request.json").read_bytes()
        record = json.loads(request)
        stage = record["stage"]
        if stage == "baseline_discovery":
            continue
        snapshot = record["data"].get("evidence_snapshot")
        key = snapshot["reference"]["artifact_id"] if snapshot is not None else stage
        response = (directory / "response.json").read_bytes()
        trace = (directory / "trace.jsonl").read_bytes()
        if key in turns:
            raise ValueError("research_resume_duplicate_turn")
        capture_record = directory / "source-timestamps.json"
        source_times = json.loads(capture_record.read_bytes()) if capture_record.is_file() else {}
        captured = (
            source_times.get("provider_response_captured_at")
            or datetime.fromtimestamp((directory / "response.json").stat().st_mtime, UTC).isoformat()
        )
        turns[key] = RetainedTurn(request, response, trace, directory, captured)
        if snapshot is not None:
            stamps["snapshot-" + key] = snapshot["created_at"]
            if datetime.fromisoformat(captured) < datetime.fromisoformat(snapshot["created_at"]):
                raise ValueError("research_resume_capture_time_invalid")
            if json.loads(response).get("kind") in {"final", "unchanged", "delta"}:
                stamps["artifact-" + key] = source_times.get("artifact_created_at") or captured
        candidates = [*record["data"].get("specialists", {}).values(), *record["data"].get("summaries", {}).values()]
        if "main" in record["data"]:
            candidates.append(record["data"]["main"])
        own_previous = record["data"].get("own_previous_analysis")
        if isinstance(own_previous, dict):
            candidates.append(own_previous)
        for artifact in candidates:
            reference = artifact.get("evidence_snapshot_reference")
            if reference is not None:
                exact_artifact_stamps["artifact-" + reference["artifact_id"]] = artifact["created_at"]
    stamps.update(exact_artifact_stamps)
    return turns, stamps


def load_acquisition_turns(root: Path) -> list[tuple[AcquisitionRequest, AcquisitionResult, dict[str, bytes]]]:
    """Retain the original acquisition call order for exact execution replay before continuation."""
    entries: list[tuple[AcquisitionRequest, AcquisitionResult, dict[str, bytes]]] = []
    baseline_count = (
        json.loads((root / "baseline/manifest.json").read_bytes())["request_count"]
        if any(root.glob("[0-9][0-9][0-9][0-9]-baseline_discovery"))
        else 0
    )
    for directory in sorted((root / "acquisitions").glob("[0-9]*"), key=lambda value: int(value.name)):
        if not (directory / "result.json").is_file():
            continue
        request = AcquisitionRequest.model_validate_json((directory / "request.json").read_bytes())
        result = AcquisitionResult.model_validate_json((directory / "result.json").read_bytes())
        files = _payload(directory / "files")
        validate_acquisition(request, result, files)
        if int(directory.name) < baseline_count:
            baseline_request = AcquisitionRequest.model_validate_json(
                (root / "baseline/requests" / (directory.name + ".json")).read_bytes()
            )
            baseline_result = AcquisitionResult.model_validate_json(
                (root / "baseline/results" / (directory.name + ".json")).read_bytes()
            )
            if request != baseline_request or result != baseline_result:
                raise ValueError("research_resume_baseline_acquisition_mismatch")
            continue
        entries.append((request, result, files))
    return entries
