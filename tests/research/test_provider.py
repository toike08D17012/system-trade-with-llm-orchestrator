"""Restricted provider invocations reject failures and malformed structured responses."""

import json
import subprocess
from pathlib import Path
from typing import Literal, cast

import pytest

from stock_research_llm_orchestrator.agents.research_provider import invoke_research
from stock_research_llm_orchestrator.research.workflow import ReportTranslation, ResearchRequest


def _request(provider: str) -> ResearchRequest:
    assert provider in {"codex", "claude"}
    return ResearchRequest(
        "translation",
        cast(Literal["codex", "claude"], provider),
        "Translate only.",
        {"text": "Private fixture input"},
        ReportTranslation,
    )


@pytest.mark.parametrize("provider", ["codex", "claude"])
def test_fresh_provider_turn_allows_web_and_keeps_data_off_argv(provider: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Private stdin reaches a fresh process with native Web tools and no peer workspace."""

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        assert "Private fixture input" not in " ".join(command)
        assert b"Private fixture input" in cast(bytes, kwargs["input"])
        assert Path(cast(Path, kwargs["cwd"])).is_dir()
        result = {"translations": [{"key": "text", "text": "検証用"}]}
        if provider == "codex":
            assert "--ignore-user-config" in command
            assert 'web_search="live"' in command
            assert "features.shell_tool=false" in command
            assert "features.multi_agent=false" in command
            assert "--ephemeral" in command
            Path(command[command.index("--output-last-message") + 1]).write_text(json.dumps(result))
            stdout = b""
        else:
            assert command[command.index("--tools") + 1] == "WebSearch,WebFetch"
            assert command[command.index("--allowedTools") + 1] == "WebSearch,WebFetch"
            assert "--safe-mode" in command and "--strict-mcp-config" in command
            assert "--no-session-persistence" in command
            stdout = json.dumps(
                {"type": "result", "subtype": "success", "is_error": False, "structured_output": result}
            ).encode()
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert json.loads(invoke_research(_request(provider)))["translations"][0]["text"] == "検証用"


@pytest.mark.parametrize("provider", ["codex", "claude"])
def test_provider_failure_is_not_adopted(provider: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """No response or fallback is accepted after nonzero process exit."""
    monkeypatch.setattr(
        subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1, b"", b"secret")
    )
    with pytest.raises(RuntimeError, match="^research_provider_failed$"):
        invoke_research(_request(provider))


@pytest.mark.parametrize(
    "envelope",
    [
        {},
        {"type": "result", "subtype": "error", "is_error": True},
        {"type": "result", "subtype": "success", "is_error": False, "result": "{}"},
    ],
)
def test_claude_requires_successful_structured_output(
    envelope: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    """An apparently successful process must still contain the expected result envelope."""
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, json.dumps(envelope).encode(), b""),
    )
    with pytest.raises(ValueError, match="research_provider_output_invalid"):
        invoke_research(_request("claude"))


def test_preflight_rejects_missing_authentication_without_exposing_provider_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both provider checks are local and no analysis starts after an authentication failure."""
    from stock_research_llm_orchestrator.agents.research_provider import ProviderAuthenticationError, preflight_research

    calls = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        calls.append(command)
        if command[0] == "codex":
            return subprocess.CompletedProcess(command, 0, b"", b"")
        return subprocess.CompletedProcess(command, 1, b"private account data", b"private error")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(ProviderAuthenticationError) as error:
        preflight_research()
    assert error.value.provider == "claude"
    assert "private" not in str(error.value)
    assert calls == [["codex", "login", "status"], ["claude", "auth", "status", "--json"]]


@pytest.mark.parametrize("provider", ["codex", "claude"])
def test_native_web_trace_preserves_actual_tool_events(provider: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Web-enabled execution captures the provider trace, not an agent's self-reported search claim."""
    from stock_research_llm_orchestrator.agents.research_provider import invoke_web_research, native_web_calls

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        body = {"translations": [{"key": "text", "text": "日本語"}]}
        events: list[dict[str, object]]
        if provider == "codex":
            assert "--json" in command and 'web_search="live"' in command
            Path(command[command.index("--output-last-message") + 1]).write_text(json.dumps(body))
            events = [{"type": "item.started", "item": {"id": "search-1", "type": "web_search"}}]
        else:
            assert command[command.index("--output-format") + 1] == "stream-json"
            assert "--verbose" in command
            assert command[command.index("--allowedTools") + 1] == "WebSearch,WebFetch"
            events = [
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "tool_use", "name": "WebSearch", "id": "search-1"}]},
                },
                {"type": "result", "subtype": "success", "is_error": False, "structured_output": body},
            ]
        return subprocess.CompletedProcess(command, 0, b"\n".join(json.dumps(e).encode() for e in events), b"")

    monkeypatch.setattr(subprocess, "run", run)
    result = invoke_web_research(_request(provider))
    assert native_web_calls(result.trace) == 1
    assert json.loads(result.body)["translations"][0]["text"] == "日本語"
    assert result.retained_request is None


def test_invalid_structured_response_retains_body_trace_and_safe_problems(monkeypatch: pytest.MonkeyPatch) -> None:
    """Model validation failure preserves real output for diagnosis after temporary workspace cleanup."""
    from stock_research_llm_orchestrator.agents.research_provider import (
        ProviderResponseValidationError,
        invoke_web_research,
    )

    body = b'{"translations":"invalid private response"}'
    trace = b'{"type":"item.started","item":{"type":"web_search"}}\n'

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        Path(command[command.index("--output-last-message") + 1]).write_bytes(body)
        return subprocess.CompletedProcess(command, 0, trace, b"")

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(ProviderResponseValidationError) as failure:
        invoke_web_research(_request("codex"))
    assert failure.value.body == body
    assert failure.value.trace == trace
    assert failure.value.problems[0]["loc"] == ("translations",)
    assert "input" not in failure.value.problems[0]
    assert "invalid private response" not in str(failure.value)


@pytest.mark.parametrize(
    ("stderr", "diagnostic_code"),
    [
        (b"private credential: secret; context_length_exceeded", "context_length_exceeded"),
        (b"private credential: secret; invalid_json_schema", "invalid_json_schema"),
        (b"private credential: secret; invalid_api_key", "authentication_failed"),
        (b"private credential: secret; rate_limit_exceeded", "rate_limit_exceeded"),
        (b"private credential: secret; insufficient_quota", "quota_exceeded"),
        (b"private credential: secret; unexpected error", "unknown"),
    ],
)
def test_failed_execution_retains_native_trace_and_only_classified_stderr(
    monkeypatch: pytest.MonkeyPatch, stderr: bytes, diagnostic_code: str
) -> None:
    """An unsuccessful provider cannot erase actual trace or expose arbitrary stderr diagnostics."""
    from stock_research_llm_orchestrator.agents.research_provider import ProviderExecutionError, invoke_web_research

    trace = b'{"type":"error","message":"native execution failed"}\n'
    monkeypatch.setattr(
        subprocess, "run", lambda command, **_kwargs: subprocess.CompletedProcess(command, 7, trace, stderr)
    )
    with pytest.raises(ProviderExecutionError, match="^research_provider_failed$") as failure:
        invoke_web_research(_request("codex"))
    assert failure.value.trace == trace
    assert failure.value.exit_status == 7
    assert failure.value.diagnostic_code == diagnostic_code
    assert "secret" not in repr(vars(failure.value))
    assert not hasattr(failure.value, "stderr")


def test_subscription_usage_limit_trace_is_classified_without_retaining_raw_stderr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ChatGPT subscription limit uses the same safe quota outcome as API exhaustion."""
    from stock_research_llm_orchestrator.agents.research_provider import ProviderExecutionError, invoke_web_research

    trace = (
        json.dumps({"type": "error", "message": "You’ve hit your usage limit. Try again at 10:15 PM."}).encode() + b"\n"
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 1, trace, b"private credential: secret"),
    )
    with pytest.raises(ProviderExecutionError) as failure:
        invoke_web_research(_request("codex"))
    assert failure.value.diagnostic_code == "quota_exceeded"
    assert failure.value.trace == trace
    assert "secret" not in repr(vars(failure.value))
    assert "10:15 PM" not in str(failure.value)


@pytest.mark.parametrize("provider", ["codex", "claude"])
def test_codex_requires_explicit_nested_optional_fields_without_changing_public_schema(
    provider: Literal["codex", "claude"], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Acquisition continuation schemas satisfy strict Codex output rules at every object depth."""
    from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.acquisition_analysis import (
        AcquisitionSpecialistTurn,
    )

    public_schema = AcquisitionSpecialistTurn.model_json_schema()
    assert "state" not in public_schema["required"]
    assert "url" not in public_schema["$defs"]["AcquisitionRequest"]["required"]
    body = {
        "kind": "acquire",
        "requests": [
            {
                "operation": "listing",
                "security_code": "7203",
                "purpose": "Verify current security identity.",
                "url": None,
                "start": None,
                "end": None,
                "classification": None,
            },
            {
                "operation": "search_evidence",
                "evidence_id": "issuer-ir-retained",
                "query": "dividend coverage",
                "start_line": None,
                "end_line": None,
            },
            {
                "operation": "read_evidence",
                "evidence_id": "issuer-ir-retained",
                "query": None,
                "start_line": 1,
                "end_line": 20,
            },
        ],
        "state": "Waiting for original listing data.",
        "payload": None,
    }

    def run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        if provider == "codex":
            schema_path = Path(command[command.index("--output-schema") + 1])
            native_schema = json.loads(schema_path.read_text())

            def check(value: object) -> None:
                if isinstance(value, dict):
                    if "properties" in value:
                        assert value["required"] == list(value["properties"])
                        assert value.get("additionalProperties") is False
                    assert "default" not in value
                    for child in value.values():
                        check(child)
                elif isinstance(value, list):
                    for child in value:
                        check(child)

            check(native_schema)
            assert "state" in native_schema["required"]
            assert "url" in native_schema["$defs"]["AcquisitionRequest"]["required"]
            assert native_schema["$defs"]["EvidenceReadRequest"]["required"] == [
                "operation",
                "evidence_id",
                "query",
                "start_line",
                "end_line",
            ]
            Path(command[command.index("--output-last-message") + 1]).write_text(json.dumps(body))
            stdout = b""
        else:
            assert json.loads(command[command.index("--json-schema") + 1]) == public_schema
            stdout = json.dumps(
                {"type": "result", "subtype": "success", "is_error": False, "structured_output": body}
            ).encode()
        return subprocess.CompletedProcess(command, 0, stdout, b"")

    monkeypatch.setattr(subprocess, "run", run)
    request = ResearchRequest("track_a_capital", provider, "Research independently.", {}, AcquisitionSpecialistTurn)
    assert json.loads(invoke_research(request)) == body
    assert AcquisitionSpecialistTurn.model_json_schema() == public_schema
