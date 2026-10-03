"""Public issuer follow-up acquisition is gated before one anonymous GET."""

import json
import socket
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from stock_research_llm_orchestrator.preparation import public_ir_follow_up, research_follow_up
from stock_research_llm_orchestrator.preparation.public_ir_follow_up import (
    acquire_public_ir_follow_up,
    validate_public_target,
)
from stock_research_llm_orchestrator.preparation.research_draft import DraftInput, DraftOutput, build_bundle
from stock_research_llm_orchestrator.preparation.research_follow_up import validate_follow_up
from stock_research_llm_orchestrator.preparation.storage import read_bundle
from stock_research_llm_orchestrator.requests.storage import RuntimeStorageError


NOW = datetime(2026, 10, 3, 12, tzinfo=UTC)


def _prior(root: Path) -> Path:
    data = DraftInput.model_validate_json(
        json.dumps(
            {
                "security_code": "1234",
                "exchange": "XTKS",
                "external_llm_transfer_allowed": True,
                "evidence": [
                    {
                        "evidence_id": "first",
                        "source_id": "issuer-ir",
                        "approval_version": 1,
                        "source_url": "https://example.com/first",
                        "retrieved_at": NOW.isoformat(),
                        "text": "Initial results.",
                    }
                ],
            }
        )
    )
    draft = DraftOutput.model_validate_json(
        json.dumps(
            {
                "claims": [],
                "missing_information": ["Segment growth"],
                "follow_up_requests": [
                    {
                        "question": "What grew by segment?",
                        "reason": "Initial results omit segments.",
                        "suggested_source_url": None,
                    }
                ],
            }
        )
    )
    prior = root / "prior"
    prior.mkdir()
    for name, body in build_bundle(data, draft).items():
        (prior / name).write_bytes(body)
    return prior


def _public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", 443))],
    )


def test_fetch_public_ir_publishes_replayable_follow_up(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """One admitted GET stores raw bytes and a next-pass input."""
    _public_dns(monkeypatch)
    seen: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, content=b"<h1>Segments</h1><p>Cars 10</p>", headers={"Content-Type": "text/html"})

    output = acquire_public_ir_follow_up(
        prior=_prior(tmp_path),
        url="https://example.com/second",
        allowed_host="example.com",
        request_index=0,
        runtime=tmp_path / "runtime",
        runs=tmp_path / "runs",
        destination="next",
        task_id="public-ir-test",
        clock=lambda: NOW,
        transport=httpx.MockTransport(respond),
    )
    assert seen == ["https://example.com/second"]
    files = read_bundle(output)
    validate_follow_up(files)
    assert DraftInput.model_validate_json(files["input.json"]).evidence[1].text == "Segments\nCars 10"
    assert list((tmp_path / "runs/public-ir-test").rglob("body.bin"))


def test_target_rejects_private_address_and_host_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """An LLM-proposed URL cannot reach a local target or a different host."""
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))],
    )
    with pytest.raises(ValueError, match="public_ir_non_public_address"):
        validate_public_target("https://example.com/report", "example.com")
    with pytest.raises(ValueError, match="public_ir_url_invalid"):
        validate_public_target("https://other.example/report", "example.com")


def test_redirect_is_not_followed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A redirect cannot turn a checked URL into an unchecked second request."""
    _public_dns(monkeypatch)
    seen: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(302, headers={"Location": "https://other.example/report", "Content-Type": "text/html"})

    with pytest.raises(ValueError):
        acquire_public_ir_follow_up(
            prior=_prior(tmp_path),
            url="https://example.com/second",
            allowed_host="example.com",
            request_index=0,
            runtime=tmp_path / "runtime",
            runs=tmp_path / "runs",
            destination="next",
            task_id="public-ir-test",
            clock=lambda: NOW,
            transport=httpx.MockTransport(respond),
        )
    assert seen == ["https://example.com/second"]
    assert not (tmp_path / "runs/next").exists()


def test_gate_blocks_second_fetch_with_shared_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A second task cannot bypass the durable public IR rate gate."""
    _public_dns(monkeypatch)
    seen: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, content=b"Segment results", headers={"Content-Type": "text/plain"})

    prior = _prior(tmp_path)
    transport = httpx.MockTransport(respond)

    def fetch(destination: str, task_id: str) -> Path:
        return acquire_public_ir_follow_up(
            prior=prior,
            url="https://example.com/second",
            allowed_host="example.com",
            request_index=0,
            runtime=tmp_path / "runtime",
            runs=tmp_path / "runs",
            destination=destination,
            task_id=task_id,
            clock=lambda: NOW,
            transport=transport,
        )

    fetch("first", "first-task")
    with pytest.raises(RuntimeStorageError, match="gate_blocked"):
        fetch("second", "second-task")
    assert seen == ["https://example.com/second"]


def test_invalid_destination_is_rejected_before_get(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Invalid output paths fail before any network operation."""
    _public_dns(monkeypatch)
    seen: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, content=b"unexpected")

    with pytest.raises(ValueError, match="public_ir_destination_invalid"):
        acquire_public_ir_follow_up(
            prior=_prior(tmp_path),
            url="https://example.com/second",
            allowed_host="example.com",
            request_index=0,
            runtime=tmp_path / "runtime",
            runs=tmp_path / "runs",
            destination="../outside",
            task_id="public-ir-test",
            clock=lambda: NOW,
            transport=httpx.MockTransport(respond),
        )
    assert not seen


def test_fetch_accepts_response_above_former_fifty_mb_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A valid response is not rejected solely for exceeding the old byte cap."""
    _public_dns(monkeypatch)
    body = b"x" * 50_000_001

    def short_extraction(raw: bytes, media_type: str) -> str:
        assert len(raw) == len(body)
        assert media_type == "text/plain"
        return "Large document retained"

    monkeypatch.setattr(public_ir_follow_up, "extract_text", short_extraction)
    monkeypatch.setattr(research_follow_up, "extract_text", short_extraction)
    output = acquire_public_ir_follow_up(
        prior=_prior(tmp_path),
        url="https://example.com/large",
        allowed_host="example.com",
        request_index=0,
        runtime=tmp_path / "runtime",
        runs=tmp_path / "runs",
        destination="next",
        task_id="large-ir-test",
        clock=lambda: NOW,
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, content=body, headers={"Content-Type": "text/plain"})
        ),
    )
    assert (output / "source.bin").stat().st_size == len(body)
    validate_follow_up(read_bundle(output))
