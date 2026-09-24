"""Pure BOJ FM08/FXERD04 code-API intent and parser."""

import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Literal

from pydantic import Field, ValidationError, field_validator, model_validator

from stock_research_llm_orchestrator.contracts.base import StrictContractModel
from stock_research_llm_orchestrator.sources.protocol import (
    BoundedSourceResponse,
    CredentialFreeSourceIntent,
    SourceParameter,
)


_SOURCE_ID = "boj"
_OPERATION = "fx-daily-code"
_ORIGIN = "www.stat-search.boj.or.jp"
_RESOURCE = "api-v1-getDataCode"
_MEDIA_TYPE = "application/json"
_ENCODING = "utf-8"


class BojCodeApiParseError(ValueError):
    """Sanitized failure raised for invalid BOJ code-API output."""


class BojFxObservation(StrictContractModel):
    """One explicit Tokyo-date FX observation, including a missing value."""

    observed_on: str
    value: Decimal | None

    @field_validator("observed_on")
    @classmethod
    def validate_observed_on(cls, value: str) -> str:
        """Require a real ISO calendar date."""
        date.fromisoformat(value)
        return value


class BojFxDailySeries(StrictContractModel):
    """Approved BOJ daily USD/JPY series with provider metadata."""

    database: Literal["FM08"]
    series_code: Literal["FXERD04"]
    frequency: Literal["DAILY"]
    timezone: Literal["Asia/Tokyo"] = "Asia/Tokyo"
    fixing_hour: Literal[17] = 17
    unit: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=512)
    category: str = Field(min_length=1, max_length=512)
    generated_at: str
    last_update: str
    observations: tuple[BojFxObservation, ...]

    @field_validator("generated_at")
    @classmethod
    def validate_generated_at(cls, value: str) -> str:
        """Require the provider generation timestamp in JST."""
        parsed = datetime.fromisoformat(value)
        if parsed.utcoffset() != timedelta(hours=9):
            raise ValueError("boj_generated_at_not_jst")
        return value

    @field_validator("last_update")
    @classmethod
    def validate_last_update(cls, value: str) -> str:
        """Require a real ISO provider revision date."""
        date.fromisoformat(value)
        return value

    @model_validator(mode="after")
    def require_ordered_unique_observations(self) -> BojFxDailySeries:
        """Reject duplicate or reordered observations."""
        dates = tuple(item.observed_on for item in self.observations)
        if dates != tuple(sorted(dates)) or len(dates) != len(set(dates)):
            raise ValueError("boj_observations_not_canonical")
        return self


class _RawParameters(StrictContractModel):
    FORMAT: Literal["JSON"]
    LANG: Literal["EN"]
    DB: Literal["FM08"]
    STARTDATE: str
    ENDDATE: str
    STARTPOSITION: str


class _RawValues(StrictContractModel):
    SURVEY_DATES: tuple[int, ...] = Field(strict=False)
    VALUES: tuple[Decimal | int | None, ...] = Field(strict=False)

    @model_validator(mode="after")
    def require_aligned_values(self) -> _RawValues:
        if len(self.SURVEY_DATES) != len(self.VALUES):
            raise ValueError("boj_observation_length_mismatch")
        return self


class _RawSeries(StrictContractModel):
    SERIES_CODE: Literal["FXERD04"]
    NAME_OF_TIME_SERIES: str
    UNIT: str
    FREQUENCY: Literal["DAILY"]
    CATEGORY: str
    LAST_UPDATE: int
    VALUES: _RawValues


class _RawResponse(StrictContractModel):
    STATUS: Literal[200]
    MESSAGEID: str
    MESSAGE: str
    DATE: str
    PARAMETER: _RawParameters
    NEXTPOSITION: None
    RESULTSET: tuple[_RawSeries, ...] = Field(strict=False, min_length=1, max_length=1)


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise BojCodeApiParseError("boj_code_api_invalid")
        result[key] = value
    return result


class BojFxCodeAdapter:
    """Build and parse only the approved BOJ daily USD/JPY code request."""

    @property
    def source_id(self) -> str:
        """Return the immutable BOJ source identifier."""
        return _SOURCE_ID

    def build_intent(self, operation: str, parameters: tuple[SourceParameter, ...]) -> CredentialFreeSourceIntent:
        """Build one canonical month-bounded code-API intent."""
        values = {parameter.name: parameter.value for parameter in parameters}
        if operation != _OPERATION or tuple(values) != (
            "code",
            "db",
            "end_date",
            "format",
            "lang",
            "start_date",
        ):
            raise ValueError("invalid_boj_fx_intent")
        if (
            values["code"] != "FXERD04"
            or values["db"] != "FM08"
            or values["format"] != "json"
            or values["lang"] != "en"
        ):
            raise ValueError("invalid_boj_fx_intent")
        try:
            start = _parse_month(values["start_date"])
            end = _parse_month(values["end_date"])
        except ValueError:
            raise ValueError("invalid_boj_fx_intent") from None
        if start > end:
            raise ValueError("invalid_boj_fx_intent")
        return CredentialFreeSourceIntent(
            source_id=_SOURCE_ID,
            operation=_OPERATION,
            origin=_ORIGIN,
            resource_key=_RESOURCE,
            parameters=parameters,
        )

    def parse(self, response: BoundedSourceResponse) -> BojFxDailySeries:
        """Parse exact UTF-8 JSON while retaining explicit missing values."""
        if response.media_type != _MEDIA_TYPE or response.encoding.lower() != _ENCODING:
            raise BojCodeApiParseError("boj_code_api_invalid")
        try:
            payload = json.loads(
                response.body.decode(_ENCODING, errors="strict"),
                object_pairs_hook=_unique_json_object,
                parse_float=Decimal,
                parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("invalid_constant")),
            )
            raw = _RawResponse.model_validate(payload)
            series = raw.RESULTSET[0]
            observations = tuple(
                BojFxObservation(
                    observed_on=_parse_day(raw_date).isoformat(),
                    value=None if raw_value is None else Decimal(raw_value),
                )
                for raw_date, raw_value in zip(series.VALUES.SURVEY_DATES, series.VALUES.VALUES, strict=True)
            )
            return BojFxDailySeries(
                database=raw.PARAMETER.DB,
                series_code=series.SERIES_CODE,
                frequency=series.FREQUENCY,
                unit=series.UNIT,
                name=series.NAME_OF_TIME_SERIES,
                category=series.CATEGORY,
                generated_at=raw.DATE,
                last_update=_parse_day(series.LAST_UPDATE).isoformat(),
                observations=observations,
            )
        except (BojCodeApiParseError, KeyError, TypeError, UnicodeError, ValueError, ValidationError) as exc:
            if isinstance(exc, BojCodeApiParseError):
                raise
            raise BojCodeApiParseError("boj_code_api_invalid") from None


def _parse_month(value: str) -> date:
    if len(value) != 6 or not value.isascii() or not value.isdecimal():
        raise ValueError("invalid_boj_month")
    return date(int(value[:4]), int(value[4:]), 1)


def _parse_day(value: int) -> date:
    text = str(value)
    if len(text) != 8:
        raise ValueError("invalid_boj_day")
    return date(int(text[:4]), int(text[4:6]), int(text[6:]))
