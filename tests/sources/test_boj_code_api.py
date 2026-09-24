"""Tests for the pure BOJ FM08/FXERD04 code-API adapter."""

import hashlib
import json
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from stock_research_llm_orchestrator.sources import (
    BojCodeApiParseError,
    BojFxCodeAdapter,
    BoundedSourceResponse,
    SourceParameter,
)


FIXTURE = Path(__file__).parents[1] / "fixtures/sources/boj/fxerd04.json"


def _parameters() -> tuple[SourceParameter, ...]:
    return (
        SourceParameter(name="code", value="FXERD04"),
        SourceParameter(name="db", value="FM08"),
        SourceParameter(name="end_date", value="202609"),
        SourceParameter(name="format", value="json"),
        SourceParameter(name="lang", value="en"),
        SourceParameter(name="start_date", value="202609"),
    )


def _response(body: bytes) -> BoundedSourceResponse:
    return BoundedSourceResponse(
        physical_attempt_id="attempt-boj-1",
        body=body,
        sha256=hashlib.sha256(body).hexdigest(),
        media_type="application/json",
        encoding="utf-8",
    )


def test_builds_only_approved_credential_free_code_intent() -> None:
    """Fix the database, series, format, language, and endpoint identity."""
    intent = BojFxCodeAdapter().build_intent("fx-daily-code", _parameters())

    assert intent.source_id == "boj"
    assert intent.origin == "www.stat-search.boj.or.jp"
    assert intent.resource_key == "api-v1-getDataCode"
    assert intent.parameters == _parameters()


@pytest.mark.parametrize(
    "parameters",
    [
        _parameters()[:-1],
        tuple(item.model_copy(update={"value": "OTHER"}) if item.name == "code" else item for item in _parameters()),
        tuple(
            item.model_copy(update={"value": "202613"}) if item.name == "end_date" else item for item in _parameters()
        ),
        tuple(
            item.model_copy(update={"value": "202610"}) if item.name == "start_date" else item for item in _parameters()
        ),
    ],
)
def test_rejects_unapproved_or_invalid_intent(parameters: tuple[SourceParameter, ...]) -> None:
    """Reject incomplete, different-series, invalid-month, and reversed requests."""
    with pytest.raises(ValueError, match="^invalid_boj_fx_intent$"):
        BojFxCodeAdapter().build_intent("fx-daily-code", parameters)


def test_parses_daily_values_and_preserves_missing_observation() -> None:
    """Preserve decimal precision and explicit null without gap filling."""
    parsed = BojFxCodeAdapter().parse(_response(FIXTURE.read_bytes()))

    assert parsed.database == "FM08"
    assert parsed.series_code == "FXERD04"
    assert parsed.timezone == "Asia/Tokyo"
    assert parsed.fixing_hour == 17
    assert parsed.last_update == "2026-09-24"
    assert tuple(item.observed_on for item in parsed.observations) == (
        "2026-09-21",
        "2026-09-22",
        "2026-09-23",
    )
    assert tuple(item.value for item in parsed.observations) == (Decimal("147.25"), None, Decimal("148"))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update(STATUS=500),
        lambda value: value.update(NEXTPOSITION=2),
        lambda value: value["PARAMETER"].update(DB="OTHER"),
        lambda value: value["RESULTSET"][0].update(SERIES_CODE="OTHER"),
        lambda value: value["RESULTSET"][0]["VALUES"].update(VALUES=[1]),
        lambda value: value["RESULTSET"][0]["VALUES"].update(SURVEY_DATES=[20260923, 20260921, 20260922]),
    ],
)
def test_rejects_provider_error_pagination_identity_and_noncanonical_values(
    mutate: Callable[[dict[str, Any]], None],
) -> None:
    """Fail closed on provider errors, truncation, identity drift, and ambiguity."""
    value: dict[str, Any] = json.loads(FIXTURE.read_bytes())
    mutate(value)

    with pytest.raises(BojCodeApiParseError, match="^boj_code_api_invalid$"):
        BojFxCodeAdapter().parse(_response(json.dumps(value).encode()))


@pytest.mark.parametrize("body", [b"", b"null", b'{"STATUS":200,"STATUS":500}', b'{"value":NaN}'])
def test_rejects_empty_null_duplicate_and_nonfinite_json(body: bytes) -> None:
    """Map unsafe JSON variants to one sanitized parser failure."""
    with pytest.raises(BojCodeApiParseError, match="^boj_code_api_invalid$"):
        BojFxCodeAdapter().parse(_response(body))
