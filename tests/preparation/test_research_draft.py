"""A provisional model response stays bound to supplied evidence IDs."""

import json
import subprocess
from pathlib import Path

import pytest

from stock_research_llm_orchestrator.preparation.research_draft import (
    DraftInput,
    DraftOutput,
    build_bundle,
    run_codex,
    validate_bundle,
    validate_transfer,
)


def _input() -> DraftInput:
    return DraftInput.model_validate_json(
        json.dumps(
            {
                "security_code": "1234",
                "exchange": "XTKS",
                "external_llm_transfer_allowed": True,
                "evidence": [
                    {
                        "evidence_id": "source-1",
                        "source_id": "yfinance",
                        "approval_version": 3,
                        "source_url": "https://example.com/report",
                        "retrieved_at": "2026-10-03T00:00:00+09:00",
                        "text": "Revenue rose by 10 percent.",
                    }
                ],
            }
        )
    )


def _output(evidence_ids: list[str]) -> DraftOutput:
    return DraftOutput.model_validate_json(
        json.dumps(
            {
                "claims": [
                    {
                        "perspective": "financial",
                        "claim_type": "fact",
                        "statement": "Revenue rose by 10 percent.",
                        "evidence_ids": evidence_ids,
                        "uncertainty": "The excerpt does not explain the revenue mix.",
                    }
                ],
                "missing_information": ["Revenue by segment"],
                "follow_up_requests": [
                    {
                        "question": "What is the revenue growth by segment?",
                        "reason": "The excerpt only states consolidated growth.",
                        "suggested_source_url": None,
                    }
                ],
            }
        )
    )


def test_draft_bundle_replays_and_rejects_changed_input() -> None:
    """Input, output, and citations must remain bound after publication."""
    files = build_bundle(_input(), _output(["source-1"]))
    validate_bundle(files)
    changed = dict(files)
    changed["input.json"] += b" "
    with pytest.raises(ValueError, match="draft_bundle_binding_invalid"):
        validate_bundle(changed)
    with pytest.raises(ValueError, match="draft_evidence_reference_invalid"):
        build_bundle(_input(), _output(["invented"]))
    with pytest.raises(ValueError, match="draft_fact_without_evidence"):
        build_bundle(_input(), _output([]))


def test_run_codex_uses_read_only_workspace_and_validates_output(monkeypatch: pytest.MonkeyPatch) -> None:
    """The subprocess receives bounded evidence and cannot return invented citations."""

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert command[:4] == ["codex", "exec", "--sandbox", "read-only"]
        assert "Revenue rose by 10 percent." in str(kwargs["input"])
        assert Path(command[command.index("--output-schema") + 1]).is_file()
        output = Path(command[command.index("--output-last-message") + 1])
        output.write_text(json.dumps(_output(["source-1"]).model_dump()), encoding="utf-8")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert run_codex(_input()).claims[0].evidence_ids == ("source-1",)


def test_run_codex_rejects_failed_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """No draft is adopted when the model process fails."""
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: subprocess.CompletedProcess([], 1))
    with pytest.raises(RuntimeError, match="codex_execution_failed"):
        run_codex(_input())


def test_run_codex_rejects_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """A successful process with malformed output does not publish a draft."""

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        Path(command[command.index("--output-last-message") + 1]).write_text("not json", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(ValueError, match="draft_output_invalid"):
        run_codex(_input())


def test_draft_input_requires_transfer_attestation() -> None:
    """The operator must explicitly attest that excerpts may be sent to the LLM."""
    payload = _input().model_dump()
    payload["external_llm_transfer_allowed"] = False
    with pytest.raises(ValueError):
        DraftInput.model_validate(payload)


def test_draft_input_accepts_library_call_reference_without_http_url() -> None:
    """Library returned data uses a true call reference instead of a fabricated URL."""
    payload = _input().model_dump()
    payload["evidence"][0]["source_url"] = None
    payload["evidence"][0]["source_reference"] = "yfinance:Ticker.history:1234.T"
    with pytest.raises(ValueError, match="draft_source_hash_required"):
        DraftInput.model_validate(payload)
    payload["evidence"][0]["source_sha256"] = "a" * 64
    data = DraftInput.model_validate(payload)
    assert data.evidence[0].source_reference == "yfinance:Ticker.history:1234.T"
    payload["evidence"][0]["source_url"] = "https://example.com/report"
    with pytest.raises(ValueError, match="draft_source_reference_invalid"):
        DraftInput.model_validate(payload)


def test_draft_input_keeps_long_text_and_more_than_twenty_sources() -> None:
    """The provisional input does not reject information by fixed capacity."""
    payload = _input().model_dump()
    payload["evidence"][0]["text"] = "x" * 20_001
    payload["evidence"] = tuple(dict(payload["evidence"][0], evidence_id=f"source-{index}") for index in range(21))
    data = DraftInput.model_validate(payload)
    assert len(data.evidence) == 21
    assert len(data.evidence[0].text) == 20_001


def test_run_codex_rejects_source_without_external_transfer(monkeypatch: pytest.MonkeyPatch) -> None:
    """An input attestation cannot override the registered source approval."""
    payload = _input().model_dump()
    payload["evidence"][0]["source_id"] = "jpx"
    payload["evidence"][0]["approval_version"] = 1
    monkeypatch.setattr(subprocess, "run", lambda *_args, **_kwargs: pytest.fail("codex must not start"))
    with pytest.raises(ValueError, match="draft_source_transfer_not_approved"):
        run_codex(DraftInput.model_validate(payload))


@pytest.mark.parametrize("source_id,version", [("edinet", 6), ("issuer-ir", 1)])
def test_private_analysis_approvals_allow_relevant_excerpts(source_id: str, version: int) -> None:
    """Current source approvals allow attributed excerpts for private LLM analysis."""
    payload = _input().model_dump()
    payload["evidence"][0]["source_id"] = source_id
    payload["evidence"][0]["approval_version"] = version
    validate_transfer(DraftInput.model_validate(payload), Path("config/source-approvals"))


@pytest.mark.parametrize(
    "source_id,version",
    [("jpx", 2), ("dukascopy", 2), ("issuer-ir", 1)],
)
def test_owner_authorized_sources_allow_private_llm_analysis(source_id: str, version: int) -> None:
    """Necessary facts from current sources and public issuer IR can reach the draft."""
    payload = _input().model_dump()
    payload["evidence"][0]["source_id"] = source_id
    payload["evidence"][0]["approval_version"] = version
    validate_transfer(DraftInput.model_validate(payload), Path("config/source-approvals"))
