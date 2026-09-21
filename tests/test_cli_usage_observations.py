"""Validate sanitized, fixed-version Agent CLI usage observations."""

import json
import re
import typing
from pathlib import Path

from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.agent_execution import (
    AgentUsageMetricV1,
)


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "cli-usage" / "v1"
OBSERVATION_KEYS = {
    "fixture_format_version",
    "observation_id",
    "pair_id",
    "provider",
    "cli_version",
    "acquired_at",
    "platform",
    "mode",
    "parent_observation_id",
    "measurement_status",
    "command_shape",
    "exit_code",
    "session_id_observed",
    "same_session_confirmed",
    "provider_observation",
    "local_wall_seconds",
    "expected_usage",
    "sanitization",
}
SANITIZATION_KEYS = {
    "allowlist_extraction",
    "provider_session_id_removed",
    "prompt_and_response_removed",
    "paths_and_account_data_removed",
    "raw_transcript_retained",
}
FORBIDDEN_PROVIDER_KEYS = {
    "account",
    "conversation_id",
    "cwd",
    "email",
    "path",
    "prompt",
    "response",
    "result",
    "session_id",
    "thread_id",
    "token",
}
PATH_PATTERN = re.compile(r"(?:/home/|/Users/|[A-Za-z]:\\|~/|file://)")
PROVIDER_OBSERVATION_KEYS = {
    "codex": {"event_types", "usage"},
    "antigravity": {"duration_seconds", "num_turns", "usage"},
}
PROVIDER_USAGE_KEYS = {
    "codex": {
        "input_tokens",
        "cached_input_tokens",
        "cache_write_input_tokens",
        "output_tokens",
        "reasoning_output_tokens",
    },
    "antigravity": {
        "input_tokens",
        "output_tokens",
        "thinking_tokens",
        "cache_read_tokens",
        "total_tokens",
    },
}


def _load(path: Path) -> dict[str, typing.Any]:
    parsed = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict)
    assert all(isinstance(key, str) for key in parsed)
    return typing.cast("dict[str, typing.Any]", parsed)


def _walk_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        keys = set(value)
        for nested in value.values():
            keys.update(_walk_keys(nested))
        return keys
    if isinstance(value, list):
        list_keys: set[str] = set()
        for nested in value:
            list_keys.update(_walk_keys(nested))
        return list_keys
    return set()


def test_fixture_index_declares_partial_provider_completion() -> None:
    """Keep verified providers separate from the deferred Claude measurement."""
    index = _load(FIXTURE_ROOT / "fixture-index.json")
    providers = {item["provider"]: item for item in index["providers"]}  # type: ignore[index]

    assert index["fixture_format_version"] == 1
    assert providers["codex"]["status"] == "verified"
    assert providers["antigravity"]["status"] == "verified"
    assert providers["claude-code"] == {
        "provider": "claude-code",
        "status": "blocked_subscription",
        "fixtures": [],
    }


def test_observations_are_sanitized_and_map_to_usage_contract() -> None:
    """Validate metadata, allowlisted content, and common usage mappings."""
    index = _load(FIXTURE_ROOT / "fixture-index.json")
    fixture_paths = [
        FIXTURE_ROOT / fixture
        for provider in index["providers"]  # type: ignore[index]
        for fixture in provider["fixtures"]
    ]

    for path in fixture_paths:
        observation = _load(path)
        assert set(observation) == OBSERVATION_KEYS
        assert observation["fixture_format_version"] == 1
        assert observation["measurement_status"] == "verified"
        assert observation["cli_version"]
        assert observation["acquired_at"]
        assert observation["exit_code"] == 0
        assert observation["session_id_observed"] is True
        assert observation["local_wall_seconds"] > 0  # type: ignore[operator]

        sanitization = observation["sanitization"]
        assert isinstance(sanitization, dict)
        assert set(sanitization) == SANITIZATION_KEYS
        assert sanitization == {
            "allowlist_extraction": True,
            "provider_session_id_removed": True,
            "prompt_and_response_removed": True,
            "paths_and_account_data_removed": True,
            "raw_transcript_retained": False,
        }

        provider_observation = observation["provider_observation"]
        assert isinstance(provider_observation, dict)
        provider_name = observation["provider"]
        assert isinstance(provider_name, str)
        assert set(provider_observation) == PROVIDER_OBSERVATION_KEYS[provider_name]
        assert set(provider_observation["usage"]) == PROVIDER_USAGE_KEYS[provider_name]
        assert not (_walk_keys(provider_observation) & FORBIDDEN_PROVIDER_KEYS)
        serialized = json.dumps(observation, ensure_ascii=False)
        assert not PATH_PATTERN.search(serialized)

        usage = tuple(AgentUsageMetricV1.model_validate(item) for item in observation["expected_usage"])
        metric_names = [item.metric for item in usage]
        assert len(metric_names) == len(set(metric_names))
        for metric in usage:
            if metric.availability.value != "retrieved":
                assert metric.value is None


def test_new_and_resume_form_same_sanitized_pair() -> None:
    """Require each verified provider to have one linked new/resume pair."""
    index = _load(FIXTURE_ROOT / "fixture-index.json")

    for provider in index["providers"]:  # type: ignore[index]
        if provider["status"] != "verified":
            continue
        observations = [_load(FIXTURE_ROOT / path) for path in provider["fixtures"]]
        by_mode = {item["mode"]: item for item in observations}
        assert set(by_mode) == {"new", "resume"}

        new = by_mode["new"]
        resume = by_mode["resume"]
        assert new["provider"] == resume["provider"] == provider["provider"]
        assert new["cli_version"] == resume["cli_version"]
        assert new["pair_id"] == resume["pair_id"]
        assert new["parent_observation_id"] is None
        assert resume["parent_observation_id"] == new["observation_id"]
        assert new["same_session_confirmed"] is None
        assert resume["same_session_confirmed"] is True


def test_unconfirmed_provider_values_are_not_converted_to_run_deltas() -> None:
    """Retain raw observations without inventing numeric common-contract values."""
    for path in FIXTURE_ROOT.glob("*/*.json"):
        observation = _load(path)
        usage = [AgentUsageMetricV1.model_validate(item) for item in observation["expected_usage"]]
        assert not any(metric.metric.endswith("_run_delta") for metric in usage)
        for metric in usage:
            if metric.metric in {"input_tokens", "provider_duration", "turn_count"}:
                assert metric.scope.value == "unknown"
                assert metric.meaning_confirmed is False
                assert metric.value is None
