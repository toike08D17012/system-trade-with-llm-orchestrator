"""Detailed-analysis task and security contracts for version 1."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, field_validator, model_validator

from stock_research_llm_orchestrator.contracts.base import Identifier, NonEmptyString, StrictContractModel, Timestamp


SecurityCode = Annotated[str, StringConstraints(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9]+$")]
CurrencyCode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
LanguageTag = Annotated[str, StringConstraints(pattern=r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")]


class AnalysisHorizon(StrEnum):
    """Approved investment evaluation horizons."""

    MEDIUM_TERM = "medium_term"
    LONG_TERM = "long_term"


class TaskOrigin(StrEnum):
    """Supported origins for a detailed-analysis task."""

    HUMAN_SELECTED = "human_selected"
    CANDIDATE_POOL = "candidate_pool"


class HumanDocumentType(StrEnum):
    """Human-facing documents whose language can be overridden."""

    FINAL_REPORT = "final_report"
    HUMAN_DECISION_REQUEST = "human_decision_request"


AnalysisHorizonValue = Annotated[AnalysisHorizon, Field(strict=False)]
HumanDocumentTypeValue = Annotated[HumanDocumentType, Field(strict=False)]


class MarketIdentityV1(StrictContractModel):
    """Approved market scope for the initial detailed-analysis MVP."""

    mic: Literal["XTKS"]
    timezone: Literal["Asia/Tokyo"]
    instrument_scope: Literal["domestic_cash_equity"]


class SecurityIdentifierV1(StrictContractModel):
    """Unambiguous security code and market identifier pair."""

    security_code: SecurityCode
    mic: Literal["XTKS"]


class SecurityInformationV1(StrictContractModel):
    """Verified security attributes produced during data preparation."""

    identifier: SecurityIdentifierV1
    name: NonEmptyString
    currency: CurrencyCode
    sector: NonEmptyString
    market_segment: NonEmptyString


class HumanDocumentLanguageOverrideV1(StrictContractModel):
    """Explicit task-level language override for human-facing documents."""

    target_documents: tuple[HumanDocumentTypeValue, ...] = Field(strict=False, min_length=1)
    language: LanguageTag
    reason: NonEmptyString

    @field_validator("target_documents")
    @classmethod
    def ensure_unique_target_documents(
        cls, target_documents: tuple[HumanDocumentType, ...]
    ) -> tuple[HumanDocumentType, ...]:
        """Reject duplicate document targets."""
        if len(set(target_documents)) != len(target_documents):
            raise ValueError("language override document targets must be unique")
        return target_documents


class TaskSafetyConstraintsV1(StrictContractModel):
    """Non-relaxable safety constraints attached to every task."""

    external_publication_allowed: Literal[False]
    brokerage_connection_allowed: Literal[False]
    order_submission_allowed: Literal[False]
    automated_trading_allowed: Literal[False]
    automated_final_investment_decision_allowed: Literal[False]


class DetailedAnalysisTaskV1(StrictContractModel):
    """Validated task accepted by the detailed-analysis workflow."""

    schema_id: Literal["detailed-analysis.detailed-analysis-task"]
    schema_version: Literal[1]
    task_id: Identifier
    parent_task_id: Identifier | None
    task_accepted_at: Timestamp
    origin: TaskOrigin = Field(strict=False)
    security: SecurityIdentifierV1
    market: MarketIdentityV1
    analysis_horizons: tuple[AnalysisHorizonValue, ...] = Field(strict=False, min_length=2, max_length=2)
    evaluation_policy_version: int = Field(ge=1)
    human_document_language_override: HumanDocumentLanguageOverrideV1 | None
    constraints: TaskSafetyConstraintsV1

    @field_validator("task_accepted_at")
    @classmethod
    def ensure_valid_accepted_at(cls, value: str) -> str:
        """Reject malformed or offset-free RFC 3339 timestamps."""
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("task_accepted_at must be a valid RFC 3339 timestamp") from exc
        if parsed.utcoffset() is None:
            raise ValueError("task_accepted_at must include a UTC offset")
        return value

    @model_validator(mode="after")
    def validate_task_scope(self) -> DetailedAnalysisTaskV1:
        """Enforce the approved initial market and two-horizon task boundary."""
        if self.security.mic != self.market.mic:
            raise ValueError("security and market MIC values must match")
        if self.analysis_horizons != (AnalysisHorizon.MEDIUM_TERM, AnalysisHorizon.LONG_TERM):
            raise ValueError("the initial MVP requires medium_term followed by long_term")
        return self
