"""A model-requested public IR document feeds one further provisional pass."""

import json
import socket
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from stock_research_llm_orchestrator.preparation import research_step
from stock_research_llm_orchestrator.preparation.public_ir_follow_up import acquire_public_ir_follow_up
from stock_research_llm_orchestrator.preparation.research_draft import (
    DraftInput,
    DraftOutput,
    build_bundle,
    validate_bundle,
)
from stock_research_llm_orchestrator.preparation.research_follow_up import validate_follow_up
from stock_research_llm_orchestrator.preparation.storage import read_bundle


NOW = datetime(2026, 10, 3, 12, tzinfo=UTC)


def _prior(root: Path, candidate: str | None = "https://example.com/segment") -> Path:
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
                        "suggested_source_url": candidate,
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


@pytest.mark.parametrize("candidate", [None, "https://other.example/segment", "https://example.com/first"])
def test_step_stops_without_new_eligible_url(tmp_path: Path, candidate: str | None) -> None:
    """Missing, cross-host, and already-read URLs never trigger a fetch."""
    prior = _prior(tmp_path, candidate)
    with pytest.raises(ValueError, match="research_step_no_eligible_ir_request"):
        research_step.select_public_ir_request(read_bundle(prior), "example.com")


def test_step_fetches_once_and_reanalyzes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The next LLM pass receives the exact newly acquired evidence."""
    prior = _prior(tmp_path)
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", 443))],
    )
    seen: list[str] = []

    def respond(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, content=b"<h1>Segments</h1><p>Cars 10</p>", headers={"Content-Type": "text/html"})

    def fetch(
        *,
        prior: Path,
        url: str,
        allowed_host: str,
        request_index: int,
        runtime: Path,
        runs: Path,
        destination: str,
        task_id: str,
    ) -> Path:
        return acquire_public_ir_follow_up(
            prior=prior,
            url=url,
            allowed_host=allowed_host,
            request_index=request_index,
            runtime=runtime,
            runs=runs,
            destination=destination,
            task_id=task_id,
            clock=lambda: NOW,
            transport=httpx.MockTransport(respond),
        )

    def run(data: DraftInput, _approval_root: Path) -> DraftOutput:
        assert data.evidence[-1].source_url == "https://example.com/segment"
        assert data.evidence[-1].text == "Segments\nCars 10"
        return DraftOutput(claims=(), missing_information=(), follow_up_requests=())

    monkeypatch.setattr(research_step, "acquire_public_ir_follow_up", fetch)
    monkeypatch.setattr(research_step, "run_codex", run)
    follow_up, draft = research_step.advance_research_step(
        prior=prior,
        allowed_host="example.com",
        runtime=tmp_path / "runtime",
        runs=tmp_path / "runs",
        follow_up_destination="follow-up",
        draft_destination="reanalyzed",
        task_id="step-test",
    )
    assert seen == ["https://example.com/segment"]
    validate_follow_up(read_bundle(follow_up))
    validate_bundle(read_bundle(draft))
    assert DraftOutput.model_validate_json((draft / "draft.json").read_bytes()).follow_up_requests == ()


def test_step_rejects_invalid_destinations_before_fetch(tmp_path: Path) -> None:
    """Unsafe or colliding names cannot start a network request."""
    with pytest.raises(ValueError, match="research_step_destination_invalid"):
        research_step.advance_research_step(
            prior=_prior(tmp_path),
            allowed_host="example.com",
            runtime=tmp_path / "runtime",
            runs=tmp_path / "runs",
            follow_up_destination="same",
            draft_destination="same",
            task_id="step-test",
        )
