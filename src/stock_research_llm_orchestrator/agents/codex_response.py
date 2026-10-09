"""Parse one completed Codex JSONL turn without adopting its claims or usage semantics."""

import json
from dataclasses import dataclass


_USAGE_FIELDS = frozenset(
    {
        "input_tokens",
        "cached_input_tokens",
        "cache_write_input_tokens",
        "output_tokens",
        "reasoning_output_tokens",
    }
)
_EVENT_TYPES = frozenset(
    {"thread.started", "turn.started", "item.started", "item.updated", "item.completed", "turn.completed"}
)


@dataclass(frozen=True, slots=True)
class CodexTurnObservation:
    """Opaque provider session, final text, and unclassified provider usage values."""

    provider_session_id: str
    final_text: str
    usage_observation: tuple[tuple[str, int], ...]


def parse_codex_jsonl(body: bytes) -> CodexTurnObservation:
    """Read one successful Codex CLI turn from its exact stdout bytes."""
    try:
        lines = body.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        raise ValueError("codex_jsonl_encoding_invalid") from None
    if not lines or any(not line.strip() for line in lines):
        raise ValueError("codex_jsonl_incomplete")
    session_id: str | None = None
    started = False
    completed = False
    messages: list[str] = []
    observed_usage: dict[str, int] = {}
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            raise ValueError("codex_jsonl_invalid") from None
        if not isinstance(event, dict) or event.get("type") not in _EVENT_TYPES or completed:
            raise ValueError("codex_jsonl_event_invalid")
        event_type = event["type"]
        if event_type == "thread.started":
            value = event.get("thread_id")
            if session_id is not None or started or not isinstance(value, str) or not value:
                raise ValueError("codex_jsonl_session_invalid")
            session_id = value
        elif event_type == "turn.started":
            if session_id is None or started:
                raise ValueError("codex_jsonl_turn_invalid")
            started = True
        elif event_type in {"item.started", "item.updated", "item.completed"}:
            if not started:
                raise ValueError("codex_jsonl_turn_invalid")
            item = event.get("item")
            if not isinstance(item, dict):
                raise ValueError("codex_jsonl_item_invalid")
            if event_type == "item.completed" and item.get("type") == "agent_message":
                text = item.get("text")
                if not isinstance(text, str) or not text:
                    raise ValueError("codex_jsonl_message_invalid")
                messages.append(text)
        else:
            if not started or not messages:
                raise ValueError("codex_jsonl_incomplete")
            usage = event.get("usage")
            if usage is not None:
                if not isinstance(usage, dict):
                    raise ValueError("codex_jsonl_usage_invalid")
                for metric in _USAGE_FIELDS & usage.keys():
                    value = usage[metric]
                    if type(value) is not int or value < 0:
                        raise ValueError("codex_jsonl_usage_invalid")
                    observed_usage[metric] = value
            completed = True
    if session_id is None or not completed:
        raise ValueError("codex_jsonl_incomplete")
    return CodexTurnObservation(
        provider_session_id=session_id,
        final_text=messages[-1],
        usage_observation=tuple(sorted(observed_usage.items())),
    )
