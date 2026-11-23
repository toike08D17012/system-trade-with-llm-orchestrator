"""Execute one fresh, restricted provider turn for provisional research."""

import json
import subprocess
import tempfile
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.agent_configuration import AgentConfigurationV1
from stock_research_llm_orchestrator.research.workflow import ResearchRequest


class ProviderAuthenticationError(RuntimeError):
    """Authentication is unavailable in the actual execution environment."""

    def __init__(self, provider: Literal["codex", "claude"]) -> None:
        """Keep only the trusted provider name, never raw authentication output."""
        self.provider = provider
        super().__init__("research_provider_authentication_required")


class ProviderResponseValidationError(ValueError):
    """Retain invalid public research output for private diagnosis and correction."""

    def __init__(self, body: bytes, problems: list[dict[str, object]]) -> None:
        """Keep source output separate from the safe public exception message."""
        self.body = body
        self.trace = b""
        self.problems = problems
        super().__init__("research_provider_response_validation_failed")


class ProviderExecutionError(RuntimeError):
    """Preserve native execution evidence while excluding raw stderr from retained diagnostics."""

    def __init__(self, trace: bytes, exit_status: int, diagnostic_code: str) -> None:
        """Keep detailed trace separate from the stable, credential-free exception message."""
        self.trace = trace
        self.exit_status = exit_status
        self.diagnostic_code = diagnostic_code
        super().__init__("research_provider_failed")


def _diagnostic_code(stdout: bytes, stderr: bytes) -> str:
    """Classify known failures without retaining or exposing arbitrary provider stderr."""
    output = (stdout + b"\n" + stderr).lower()
    for code, markers in (
        (
            "context_length_exceeded",
            (b"context_length_exceeded", b"maximum context length", b"context window exceeded"),
        ),
        ("invalid_json_schema", (b"invalid_json_schema", b"invalid schema for response_format")),
        ("authentication_failed", (b"authentication_failed", b"invalid_api_key", b"authentication required")),
        ("rate_limit_exceeded", (b"rate_limit_exceeded", b"rate limit exceeded")),
        ("quota_exceeded", (b"insufficient_quota", b"quota exceeded", b"usage limit")),
    ):
        if any(marker in output for marker in markers):
            return code
    return "unknown"


def _codex_output_schema(schema: dict[str, object]) -> dict[str, object]:
    """Require explicit nullable values at the Codex boundary without changing public contracts."""
    result = deepcopy(schema)

    def visit(value: object) -> None:
        if isinstance(value, dict):
            properties = value.get("properties")
            if isinstance(properties, dict):
                value["required"] = list(properties)
            value.pop("default", None)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(result)
    return result


def preflight_research(configuration: AgentConfigurationV1 | None = None) -> None:
    """Authenticate only the selected transports before sending any evidence."""
    providers: tuple[Literal["codex", "claude"], ...] = ("codex", "claude")
    if configuration is not None:
        selected = {configuration.agents[alias].adapter for alias in configuration.roles.assignments().values()}
        providers = tuple(provider for provider in providers if provider + "_cli" in selected)
    for provider in providers:
        command = ["codex", "login", "status"] if provider == "codex" else ["claude", "auth", "status", "--json"]
        try:
            result = subprocess.run(command, capture_output=True, check=False)
        except OSError:
            raise RuntimeError("research_provider_unavailable") from None
        if result.returncode != 0:
            raise ProviderAuthenticationError(provider)
        if provider == "claude":
            status = json.loads(result.stdout)
            if not isinstance(status, dict) or status.get("loggedIn") is not True:
                raise ProviderAuthenticationError(provider)


def invoke_research(request: ResearchRequest, *, trace_callback: Callable[[bytes], None] | None = None) -> bytes:
    """Use private stdin and a fresh workspace, rejecting failed or malformed responses."""
    if request.agent is not None and request.agent.adapter != request.provider + "_cli":
        raise ValueError("research_agent_adapter_mismatch")
    document_only = request.stage.startswith(("document_summary-", "reassessment-selection-"))
    with tempfile.TemporaryDirectory(prefix=f"research-{request.stage}-") as temporary:
        workspace = Path(temporary)
        schema = workspace / "schema.json"
        output = workspace / "response.json"
        output_schema = request.response_model.model_json_schema()
        if request.provider == "codex":
            output_schema = _codex_output_schema(output_schema)
        schema.write_text(json.dumps(output_schema), encoding="utf-8")
        prompt = (
            request.instructions + "\n\nUNTRUSTED_INPUT_DATA_JSON:\n" + json.dumps(request.data, ensure_ascii=False)
        )
        if request.provider == "codex":
            command = [
                "codex",
                "exec",
                "--ignore-user-config",
                "--ignore-rules",
                "--ephemeral",
                "--sandbox",
                "read-only",
                "--skip-git-repo-check",
                "-C",
                str(workspace),
                "-c",
                "features.shell_tool=false",
                "-c",
                "features.multi_agent=false",
                "-c",
                "features.unified_exec=false",
                "-c",
                'web_search="disabled"' if document_only else 'web_search="live"',
                "--output-schema",
                str(schema),
                "--output-last-message",
                str(output),
                "-",
            ]
        else:
            command = [
                "claude",
                "--print",
                "--safe-mode",
                "--tools",
                "" if document_only else "WebSearch,WebFetch",
                "--allowedTools",
                "" if document_only else "WebSearch,WebFetch",
                "--strict-mcp-config",
                "--mcp-config",
                '{"mcpServers":{}}',
                "--disable-slash-commands",
                "--no-chrome",
                "--no-session-persistence",
                "--setting-sources",
                "",
                "--output-format",
                "json",
                "--json-schema",
                json.dumps(request.response_model.model_json_schema()),
            ]
        if trace_callback is not None:
            if request.provider == "codex":
                command.insert(2, "--json")
            else:
                command[command.index("--output-format") + 1] = "stream-json"
                command.append("--verbose")
        if request.agent is not None:
            if request.provider == "codex":
                command[2:2] = [
                    "--model",
                    request.agent.model,
                    "-c",
                    'model_reasoning_effort="' + request.agent.reasoning.effort + '"',
                ]
            else:
                command[1:1] = ["--model", request.agent.model, "--effort", request.agent.reasoning.effort]
        try:
            completed = subprocess.run(command, input=prompt.encode(), cwd=workspace, capture_output=True, check=False)
        except OSError:
            raise RuntimeError("research_provider_unavailable") from None
        if trace_callback is not None:
            trace_callback(completed.stdout)
        if completed.returncode != 0:
            raise ProviderExecutionError(
                completed.stdout, completed.returncode, _diagnostic_code(completed.stdout, completed.stderr)
            )
        if request.provider == "codex":
            if not output.is_file():
                raise ValueError("research_provider_output_missing")
            body = output.read_bytes()
        else:
            if trace_callback is None:
                envelope = json.loads(completed.stdout)
            else:
                results = [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]
                envelopes = [value for value in results if isinstance(value, dict) and value.get("type") == "result"]
                if not envelopes:
                    raise ValueError("research_provider_output_invalid")
                envelope = envelopes[-1]
            if (
                not isinstance(envelope, dict)
                or envelope.get("type") != "result"
                or envelope.get("subtype") != "success"
                or envelope.get("is_error") is not False
                or not isinstance(envelope.get("structured_output"), dict)
            ):
                raise ValueError("research_provider_output_invalid")
            body = json.dumps(envelope["structured_output"]).encode()
        try:
            request.response_model.model_validate_json(body)
        except ValidationError as error:
            raise ProviderResponseValidationError(
                body, [dict(item) for item in error.errors(include_input=False, include_context=False)]
            ) from None
        return body


@dataclass(frozen=True)
class WebTurnResult:
    """Keep structured output, native trace, and optional exact retained turn input together."""

    body: bytes
    trace: bytes
    retained_request: bytes | None = None


def invoke_web_research(request: ResearchRequest) -> WebTurnResult:
    """Capture native search activity without interpreting it as verified financial evidence."""
    traces: list[bytes] = []
    try:
        body = invoke_research(request, trace_callback=traces.append)
    except (ProviderResponseValidationError, ProviderExecutionError) as error:
        error.trace = b"".join(traces)
        raise
    return WebTurnResult(body, traces[0])


def native_web_calls(trace: bytes) -> int:
    """Count observed native tool starts, never search claims in model-authored prose."""
    count = 0
    for line in trace.splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        if not isinstance(event, dict):
            raise ValueError("web_provider_trace_invalid")
        item = event.get("item", {})
        if event.get("type") == "item.started" and isinstance(item, dict):
            count += item.get("type") in {"web_search", "web_search_call"}
        if event.get("type") == "assistant":
            message = event.get("message", {})
            if isinstance(message, dict):
                for content in message.get("content", []):
                    if isinstance(content, dict) and content.get("type") == "tool_use":
                        count += content.get("name") in {"WebSearch", "WebFetch"}
    return count
