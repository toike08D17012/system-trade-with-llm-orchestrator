"""Codex JSONL observation follows the measured CLI event shape."""

import json

import pytest

from stock_research_llm_orchestrator.agents.codex_response import parse_codex_jsonl


def _body(events: list[dict[str, object]]) -> bytes:
    return ("\n".join(json.dumps(event) for event in events) + "\n").encode()


def _observed_events() -> list[dict[str, object]]:
    return [
        {"type": "thread.started", "thread_id": "synthetic-session"},
        {"type": "turn.started"},
        {"type": "item.completed", "item": {"id": "item-1", "type": "agent_message", "text": "OK"}},
        {
            "type": "turn.completed",
            "usage": {
                "input_tokens": 10,
                "cached_input_tokens": 0,
                "cache_write_input_tokens": 0,
                "output_tokens": 1,
                "reasoning_output_tokens": 0,
            },
        },
    ]


def test_codex_parser_retains_session_text_and_unclassified_usage() -> None:
    """Provider counts remain observations because their aggregation scope is unknown."""
    observation = parse_codex_jsonl(_body(_observed_events()))
    assert observation.provider_session_id == "synthetic-session"
    assert observation.final_text == "OK"
    assert dict(observation.usage_observation)["input_tokens"] == 10
    assert dict(observation.usage_observation)["cached_input_tokens"] == 0


@pytest.mark.parametrize(
    "events",
    [
        [{"type": "thread.started", "thread_id": "synthetic-session"}],
        _observed_events()[:-1],
        [{"type": "turn.started"}, *_observed_events()[2:]],
        [*_observed_events(), {"type": "item.completed", "item": {"type": "agent_message", "text": "late"}}],
        [*_observed_events()[:-1], {"type": "turn.failed"}],
        [
            *_observed_events()[:2],
            {"type": "item.completed", "item": {"type": "agent_message", "text": ""}},
            _observed_events()[-1],
        ],
    ],
)
def test_codex_parser_rejects_incomplete_or_failed_turn(events: list[dict[str, object]]) -> None:
    """Do not turn a partial or failed provider stream into adopted output."""
    with pytest.raises(ValueError):
        parse_codex_jsonl(_body(events))


def test_codex_parser_rejects_invalid_usage_and_non_json_lines() -> None:
    """Malformed provider data fails before a common usage record is produced."""
    events = _observed_events()
    events[-1]["usage"] = {"input_tokens": True}
    with pytest.raises(ValueError, match="codex_jsonl_usage_invalid"):
        parse_codex_jsonl(_body(events))
    with pytest.raises(ValueError, match="codex_jsonl_invalid"):
        parse_codex_jsonl(b"not-json\n")
