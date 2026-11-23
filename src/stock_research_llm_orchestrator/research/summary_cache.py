"""Cache validated document navigation with one provider call per exact request."""

import fcntl
import json
import os
import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from stock_research_llm_orchestrator.agents.research_provider import ProviderResponseValidationError, WebTurnResult
from stock_research_llm_orchestrator.research.document_summary import validate_summary_response
from stock_research_llm_orchestrator.research.workflow import ResearchRequest


@dataclass(frozen=True)
class CachedSummary:
    """Retain the actual response capture time and distinguish provider work from reuse."""

    turn: WebTurnResult
    captured_at: str
    cache_hit: bool


def _json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _request_record(request: ResearchRequest) -> bytes:
    return _json(
        {
            "stage": request.stage,
            "instructions": request.instructions,
            "data": request.data,
            "output_schema": request.response_model.model_json_schema(),
            "agent": request.agent.model_dump(mode="json") if request.agent else None,
        }
    )


def _read_cache(path: Path, request: ResearchRequest, expected_request: bytes) -> CachedSummary:
    try:
        files = {
            name: (path / name).read_bytes()
            for name in ("request.json", "response.json", "trace.jsonl", "metadata.json")
        }
        expected_manifest = {name: sha256(body).hexdigest() for name, body in files.items()}
        if json.loads((path / "manifest.json").read_bytes()) != expected_manifest:
            raise ValueError("document_summary_cache_integrity_invalid")
        if files["request.json"] != expected_request:
            raise ValueError("document_summary_cache_request_mismatch")
        metadata = json.loads(files["metadata.json"])
        if not isinstance(metadata, dict) or set(metadata) != {"captured_at"}:
            raise ValueError("document_summary_cache_metadata_invalid")
        captured_at = metadata["captured_at"]
        if not isinstance(captured_at, str) or datetime.fromisoformat(captured_at).utcoffset() is None:
            raise ValueError("document_summary_cache_metadata_invalid")
        validate_summary_response(request, files["response.json"])
    except OSError as error:
        raise ValueError("document_summary_cache_incomplete") from error
    return CachedSummary(
        WebTurnResult(files["response.json"], files["trace.jsonl"], files["request.json"]),
        captured_at,
        True,
    )


def cached_summary(
    request: ResearchRequest,
    cache_root: Path,
    invoke: Callable[[ResearchRequest], WebTurnResult],
) -> CachedSummary:
    """Validate and atomically retain one summary while locking its entire generation."""
    match = re.fullmatch(r"document_summary-([0-9a-f]{64})", request.stage)
    if match is None:
        raise ValueError("document_summary_cache_stage_invalid")
    key = match[1]
    cache_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = cache_root / key
    request_record = _request_record(request)
    with (cache_root / f"{key}.lock").open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if path.exists():
            return _read_cache(path, request, request_record)
        turn = invoke(request)
        try:
            validate_summary_response(request, turn.body)
        except ValueError:
            error = ProviderResponseValidationError(turn.body, [{"code": "document_summary_response_invalid"}])
            error.trace = turn.trace
            raise error from None
        captured_at = datetime.now(UTC).isoformat()
        files = {
            "request.json": request_record,
            "response.json": turn.body,
            "trace.jsonl": turn.trace,
            "metadata.json": _json({"captured_at": captured_at}),
        }
        with tempfile.TemporaryDirectory(prefix=f".{key}-", dir=cache_root) as temporary:
            staging = Path(temporary)
            for name, body in files.items():
                (staging / name).write_bytes(body)
            (staging / "manifest.json").write_bytes(
                _json({name: sha256(body).hexdigest() for name, body in files.items()})
            )
            os.replace(staging, path)
        return CachedSummary(turn, captured_at, False)
