"""Retain validated document summaries once across calls and concurrent users."""

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from threading import Event, Lock

import pytest

from stock_research_llm_orchestrator.agents.configuration import load_agent_configuration
from stock_research_llm_orchestrator.agents.research_provider import ProviderResponseValidationError, WebTurnResult
from stock_research_llm_orchestrator.research.document_summary import summary_request
from stock_research_llm_orchestrator.research.summary_cache import CachedSummary, cached_summary
from stock_research_llm_orchestrator.research.workflow import ResearchRequest

from .test_configured import PROFILE
from .test_evidence_read import _document


def _request(text: str = "Annual disclosure\nQualification", model: str | None = None) -> ResearchRequest:
    agent = load_agent_configuration(PROFILE).agents["gpt"]
    if model is not None:
        agent = agent.model_copy(update={"model": model})
    return summary_request(_document(text), agent)


def _turn(request: ResearchRequest) -> WebTurnResult:
    return WebTurnResult(
        json.dumps(
            {
                "raw_sha256": request.data["raw_sha256"],
                "text_sha256": request.data["text_sha256"],
                "overview": "Document navigation only.",
                "topics": ["Disclosures"],
                "sections": [{"title": "Contents", "start_line": 1, "end_line": request.data["line_count"]}],
                "qualifications": [],
                "limitations": [],
            }
        ).encode(),
        b'{"type":"turn.completed"}\n',
    )


def test_repeated_summary_reuses_response_trace_request_and_capture_time(tmp_path: Path) -> None:
    """A later call returns the exact successful bytes without contacting the provider."""
    calls: list[ResearchRequest] = []

    def invoke(request: ResearchRequest) -> WebTurnResult:
        calls.append(request)
        return _turn(request)

    request = _request()
    first = cached_summary(request, tmp_path, invoke)
    second = cached_summary(request, tmp_path, invoke)
    assert calls == [request]
    assert first.cache_hit is False and second.cache_hit is True
    assert first.captured_at == second.captured_at
    assert first.turn.body == second.turn.body and first.turn.trace == second.turn.trace
    assert first.turn.retained_request is None
    assert second.turn.retained_request is not None
    record = json.loads(second.turn.retained_request)
    assert set(record) == {"stage", "instructions", "data", "output_schema", "agent"}
    assert record["stage"] == request.stage and record["data"] == request.data
    assert "https://example.com" not in second.turn.retained_request.decode()


def test_document_or_model_change_uses_a_separate_cache_entry(tmp_path: Path) -> None:
    """Changes in extracted text or model settings never adopt an older summary."""
    calls: list[str] = []

    def invoke(request: ResearchRequest) -> WebTurnResult:
        calls.append(request.stage)
        return _turn(request)

    requests = [_request(), _request("Changed disclosure"), _request(model="another-model")]
    assert len({request.stage for request in requests}) == 3
    for request in requests:
        assert cached_summary(request, tmp_path, invoke).cache_hit is False
    assert calls == [request.stage for request in requests]


def test_concurrent_cold_requests_generate_the_same_summary_only_once(tmp_path: Path) -> None:
    """The key lock covers the provider call rather than only the final write."""
    entered = Event()
    release = Event()
    second_entered = Event()
    duplicate = Event()
    calls: list[str] = []
    calls_lock = Lock()

    def invoke(request: ResearchRequest) -> WebTurnResult:
        with calls_lock:
            calls.append(request.stage)
            if len(calls) > 1:
                duplicate.set()
        entered.set()
        assert release.wait(timeout=5)
        return _turn(request)

    request = _request()

    def second_call() -> CachedSummary:
        second_entered.set()
        return cached_summary(request, tmp_path, invoke)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(cached_summary, request, tmp_path, invoke)
        assert entered.wait(timeout=5)
        second = pool.submit(second_call)
        try:
            assert second_entered.wait(timeout=5)
            assert not duplicate.wait(timeout=0.1)
        finally:
            release.set()
        results = [first.result(timeout=5), second.result(timeout=5)]
    assert calls == [request.stage]
    assert sorted(result.cache_hit for result in results) == [False, True]
    assert results[0].captured_at == results[1].captured_at


@pytest.mark.parametrize("invalid_response", [False, True])
def test_failed_or_invalid_generation_leaves_no_success_and_allows_retry(
    tmp_path: Path, invalid_response: bool
) -> None:
    """A failed provider or rejected summary cannot poison the shared success entry."""
    request = _request()

    def fail(_: ResearchRequest) -> WebTurnResult:
        if invalid_response:
            return WebTurnResult(b"{}", b"invalid summary trace")
        raise RuntimeError("provider unavailable")

    with pytest.raises((RuntimeError, ValueError)) as failure:
        cached_summary(request, tmp_path, fail)
    if invalid_response:
        assert isinstance(failure.value, ProviderResponseValidationError)
        assert failure.value.body == b"{}"
        assert failure.value.trace == b"invalid summary trace"
    assert not (tmp_path / request.stage.removeprefix("document_summary-")).exists()
    assert not any(path.is_dir() for path in tmp_path.iterdir())
    assert cached_summary(request, tmp_path, _turn).cache_hit is False


@pytest.mark.parametrize("name", ["response.json", "trace.jsonl", "request.json", "metadata.json"])
def test_cache_rejects_tampered_bytes_without_regenerating(tmp_path: Path, name: str) -> None:
    """Each preserved component has an integrity digest checked before reuse."""
    request = _request()
    cached_summary(request, tmp_path, _turn)
    entry = tmp_path / request.stage.removeprefix("document_summary-")
    path = entry / name
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="document_summary_cache_integrity_invalid"):
        cached_summary(request, tmp_path, lambda _: pytest.fail("tampered caches must not regenerate"))


def test_cache_rejects_an_exact_input_mismatch_for_the_same_key(tmp_path: Path) -> None:
    """A malformed caller cannot reuse a stage key with different request instructions."""
    request = _request()
    cached_summary(request, tmp_path, _turn)
    changed = replace(request, instructions=request.instructions + " Different instructions.")
    with pytest.raises(ValueError, match="document_summary_cache_request_mismatch"):
        cached_summary(changed, tmp_path, lambda _: pytest.fail("mismatched caches must not regenerate"))


def test_cached_summary_is_semantically_validated_even_when_its_digest_matches(tmp_path: Path) -> None:
    """Integrity hashes do not replace source-hash and line-range validation."""
    request = _request()
    cached_summary(request, tmp_path, _turn)
    entry = tmp_path / request.stage.removeprefix("document_summary-")
    response = json.loads((entry / "response.json").read_bytes())
    response["text_sha256"] = "0" * 64
    body = json.dumps(response).encode()
    (entry / "response.json").write_bytes(body)
    manifest_path = entry / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["response.json"] = sha256(body).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        cached_summary(request, tmp_path, lambda _: pytest.fail("invalid summaries must not regenerate"))
