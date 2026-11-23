"""Additive contracts for objective specialists and conditional investment conclusions."""

from typing import Annotated, Literal

from pydantic import Field, StringConstraints, field_validator, model_validator

from stock_research_llm_orchestrator.contracts.base import (
    ArtifactReferenceV1,
    Identifier,
    NonEmptyString,
    StrictContractModel,
    Timestamp,
)
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.analysis import (
    AnalysisClaimV1,
    ConfidenceValue,
    Evaluability,
    EvaluabilityValue,
)
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.evidence import _parse_rfc3339


type Objective = Literal["capital_gain", "income_gain"]
type Horizon = Literal["medium_term", "long_term"]
type MonetaryAmount = Annotated[str, StringConstraints(pattern=r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")]


class ScenarioV1(StrictContractModel):
    """Explicit future assumptions; observed purchase prices come from common input."""

    case: Literal["bear", "base", "bull"]
    story: NonEmptyString
    assumptions: tuple[NonEmptyString, ...] = Field(strict=False, min_length=1)
    evidence_ids: tuple[Identifier, ...] = Field(strict=False)
    months: int = Field(ge=12, le=60)
    currency: Literal["JPY"]
    per_share_basis: NonEmptyString
    terminal_price: MonetaryAmount | None
    cumulative_dividends: MonetaryAmount | None
    missing_information: tuple[NonEmptyString, ...] = Field(strict=False)


class ObjectiveHorizonV1(StrictContractModel):
    """One specialist's story, uncertainty and counterevidence for one horizon."""

    horizon: Horizon
    evaluability: EvaluabilityValue
    confidence: ConfidenceValue
    rationale: NonEmptyString
    thesis: NonEmptyString
    counter_thesis: NonEmptyString
    watch_conditions: tuple[NonEmptyString, ...] = Field(strict=False, min_length=1)
    missing_information: tuple[NonEmptyString, ...] = Field(strict=False)
    claims: tuple[AnalysisClaimV1, ...] = Field(strict=False, min_length=1)
    scenarios: tuple[ScenarioV1, ...] = Field(strict=False, min_length=3, max_length=3)

    @model_validator(mode="after")
    def validate_scenarios(self) -> ObjectiveHorizonV1:
        """Require all three cases without turning missing evidence into forecasts."""
        if tuple(item.case for item in self.scenarios) != ("bear", "base", "bull"):
            raise ValueError("objective_scenario_order_invalid")
        if len({item.months for item in self.scenarios}) != 1:
            raise ValueError("objective_scenario_period_mismatch")
        for item in self.scenarios:
            if (self.horizon == "medium_term" and item.months >= 24) or (
                self.horizon == "long_term" and item.months < 24
            ):
                raise ValueError("objective_scenario_horizon_invalid")
            if self.evaluability is Evaluability.NOT_EVALUABLE and (
                item.terminal_price is not None or item.cumulative_dividends is not None
            ):
                raise ValueError("objective_unevaluable_forecast_invalid")
        if self.evaluability is Evaluability.NOT_EVALUABLE and not self.missing_information:
            raise ValueError("objective_missing_information_required")
        return self


class SpecialistBody(StrictContractModel):
    """Provider-owned specialist output, without system-owned provenance."""

    horizons: tuple[ObjectiveHorizonV1, ...] = Field(strict=False, min_length=2, max_length=2)

    @model_validator(mode="after")
    def validate_horizons(self) -> SpecialistBody:
        """Require both horizons and unique claim identities."""
        if tuple(item.horizon for item in self.horizons) != ("medium_term", "long_term"):
            raise ValueError("objective_horizon_order_invalid")
        ids = [claim.claim_id for horizon in self.horizons for claim in horizon.claims]
        if len(ids) != len(set(ids)):
            raise ValueError("objective_claim_duplicate")
        return self


class SpecialistAnalysisV1(SpecialistBody):
    """One isolated objective analysis bound to exact common evidence."""

    schema_id: Literal["detailed-analysis.specialist-analysis"]
    schema_version: Literal[1]
    task_id: Identifier
    artifact_id: Identifier
    created_at: Timestamp
    provider: Literal["codex", "claude"]
    objective: Objective
    evidence_set_reference: ArtifactReferenceV1

    @field_validator("created_at")
    @classmethod
    def validate_timestamp(cls, value: str) -> str:
        """Reject impossible creation dates."""
        _parse_rfc3339(value, "created_at")
        return value

    @model_validator(mode="after")
    def validate_reference(self) -> SpecialistAnalysisV1:
        """Bind the specialist to an evidence-set contract."""
        if self.evidence_set_reference.schema_id != "detailed-analysis.evidence-set":
            raise ValueError("objective_evidence_reference_invalid")
        return self


class InvestmentConclusionV1(StrictContractModel):
    """A conditional research decision with compatible, explicit return assumptions."""

    horizon: Horizon
    primary_objective: Objective | None
    secondary_objective: Objective | None
    decision: Literal["candidate", "wait", "reject", "insufficient_information"]
    rationale: NonEmptyString
    story: NonEmptyString
    capital_allocation_consistency: NonEmptyString
    source_claim_ids: tuple[Identifier, ...] = Field(strict=False, min_length=1)
    specialist_limitations: tuple[NonEmptyString, ...] = Field(strict=False)
    unresolved_differences: tuple[NonEmptyString, ...] = Field(strict=False)
    change_conditions: tuple[NonEmptyString, ...] = Field(strict=False, min_length=1)
    missing_information: tuple[NonEmptyString, ...] = Field(strict=False)
    scenarios: tuple[ScenarioV1, ...] = Field(strict=False, min_length=3, max_length=3)

    @model_validator(mode="after")
    def validate_decision(self) -> InvestmentConclusionV1:
        """Keep objectives distinct and uncertain conclusions explicit."""
        if self.secondary_objective is not None and (
            self.primary_objective is None or self.secondary_objective == self.primary_objective
        ):
            raise ValueError("objective_duplicate_purpose")
        if self.decision != "insufficient_information" and self.primary_objective is None:
            raise ValueError("objective_primary_purpose_required")
        if self.decision == "insufficient_information" and not self.missing_information:
            raise ValueError("objective_missing_information_required")
        if tuple(item.case for item in self.scenarios) != ("bear", "base", "bull"):
            raise ValueError("objective_scenario_order_invalid")
        if len({item.months for item in self.scenarios}) != 1:
            raise ValueError("objective_scenario_period_mismatch")
        for item in self.scenarios:
            if (self.horizon == "medium_term" and item.months >= 24) or (
                self.horizon == "long_term" and item.months < 24
            ):
                raise ValueError("objective_scenario_horizon_invalid")
            if self.decision == "insufficient_information" and (
                item.terminal_price is not None or item.cumulative_dividends is not None
            ):
                raise ValueError("objective_unevaluable_forecast_invalid")
        return self


class ConclusionBody(StrictContractModel):
    """Both horizon conclusions authored by the main agent."""

    conclusions: tuple[InvestmentConclusionV1, ...] = Field(strict=False, min_length=2, max_length=2)

    @model_validator(mode="after")
    def validate_horizons(self) -> ConclusionBody:
        """Keep medium and long term conclusions separate."""
        if tuple(item.horizon for item in self.conclusions) != ("medium_term", "long_term"):
            raise ValueError("objective_horizon_order_invalid")
        return self


class InvestmentConclusionsV1(ConclusionBody):
    """Main conclusions linked to all four specialist artifacts and synthesis."""

    schema_id: Literal["detailed-analysis.investment-conclusions"]
    schema_version: Literal[1]
    task_id: Identifier
    artifact_id: Identifier
    created_at: Timestamp
    evidence_set_reference: ArtifactReferenceV1
    specialist_references: tuple[ArtifactReferenceV1, ...] = Field(strict=False, min_length=4, max_length=4)
    synthesis_reference: ArtifactReferenceV1

    @field_validator("created_at")
    @classmethod
    def validate_timestamp(cls, value: str) -> str:
        """Reject impossible creation dates."""
        _parse_rfc3339(value, "created_at")
        return value

    @model_validator(mode="after")
    def validate_references(self) -> InvestmentConclusionsV1:
        """Require distinct specialist inputs and the correct evidence and synthesis contracts."""
        if self.evidence_set_reference.schema_id != "detailed-analysis.evidence-set":
            raise ValueError("objective_evidence_reference_invalid")
        if self.synthesis_reference.schema_id != "detailed-analysis.synthesis-result":
            raise ValueError("objective_synthesis_reference_invalid")
        if any(item.schema_id != "detailed-analysis.specialist-analysis" for item in self.specialist_references):
            raise ValueError("objective_specialist_reference_invalid")
        if len({item.artifact_id for item in self.specialist_references}) != 4:
            raise ValueError("objective_specialist_reference_duplicate")
        return self
