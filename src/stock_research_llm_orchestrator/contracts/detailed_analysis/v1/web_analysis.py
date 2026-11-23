"""Independent native-Web research, distinct from machine-verified common evidence."""

from datetime import date
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator

from stock_research_llm_orchestrator.contracts.base import (
    ArtifactReferenceV1,
    Identifier,
    NonEmptyString,
    StrictContractModel,
    Timestamp,
)
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.agent_configuration import AgentDefinition
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.analysis import AnalysisClaimV1, HorizonAnalysisV1
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.configured_analysis import (
    AggregateBody,
    ConfiguredMainBody,
    ConfiguredReviewBody,
)
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.objectives import (
    InvestmentConclusionV1,
    MonetaryAmount,
    ObjectiveHorizonV1,
    ScenarioV1,
    SpecialistBody,
)


class WebSource(StrictContractModel):
    """A source reported by the research agent; not a machine-validated original."""

    source_id: Identifier
    url: NonEmptyString
    title: NonEmptyString
    supporting_excerpt: NonEmptyString
    retrieval_status: Literal["page_read", "search_result_only", "unavailable"]
    retrieved_at: Timestamp | None

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        """Accept public Web references without credentials or executable URL schemes."""
        parsed = urlsplit(value)
        if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("web_source_url_invalid")
        if any(ord(char) < 33 for char in value) or "<" in value or ">" in value:
            raise ValueError("web_source_url_invalid")
        return value


class IndependentResearch(StrictContractModel):
    """The agent's own queries, source observations and unresolved acquisition failures."""

    queries: tuple[NonEmptyString, ...] = Field(strict=False, min_length=1)
    sources: tuple[WebSource, ...] = Field(strict=False)
    unavailable_information: tuple[NonEmptyString, ...] = Field(strict=False)

    @model_validator(mode="after")
    def validate_sources(self) -> IndependentResearch:
        """Record source failures rather than fabricating a successful search."""
        ids = [source.source_id for source in self.sources]
        if len(ids) != len(set(ids)) or (not ids and not self.unavailable_information):
            raise ValueError("web_research_sources_invalid")
        return self


class WebClaim(AnalysisClaimV1):
    """A claim can cite provided evidence or explicitly reported original Web pages."""

    web_source_ids: tuple[Identifier, ...] = Field(strict=False)

    @model_validator(mode="after")
    def validate_claim_evidence(self) -> WebClaim:
        """Keep Web references separate and require a source for factual claims."""
        if self.claim_type == "fact" and not self.evidence_ids and not self.web_source_ids:
            raise ValueError("web_fact_without_reference")
        for ids in (self.evidence_ids, self.counterevidence_ids, self.web_source_ids):
            if len(ids) != len(set(ids)):
                raise ValueError("web_claim_duplicate_reference")
        return self


class WebScenario(ScenarioV1):
    """Conditional assumptions can cite independently read Web pages."""

    web_source_ids: tuple[Identifier, ...] = Field(strict=False)


class WebObjectiveHorizon(ObjectiveHorizonV1):
    """An objective specialist's horizon with independent Web references."""

    claims: tuple[WebClaim, ...] = Field(strict=False, min_length=1)

    scenarios: tuple[WebScenario, ...] = Field(strict=False, min_length=3, max_length=3)


class WebSpecialistBody(SpecialistBody):
    """Each specialist researches independently before peer conclusions are available."""

    horizons: tuple[WebObjectiveHorizon, ...] = Field(strict=False, min_length=2, max_length=2)
    research: IndependentResearch


class WebHorizonAnalysis(HorizonAnalysisV1):
    """Track-level claims retain their separate Web source provenance."""

    claims: tuple[WebClaim, ...] = Field(strict=False, min_length=1)


class WebAggregateBody(AggregateBody):
    """A track organizer can independently verify and investigate the specialist inputs."""

    horizon_analyses: tuple[WebHorizonAnalysis, ...] = Field(strict=False, min_length=2, max_length=2)
    research: IndependentResearch


class WebPriceObservation(StrictContractModel):
    """An agent-reported quote, explicitly not independently parsed or freshness-validated."""

    price: MonetaryAmount
    currency: Literal["JPY"]
    observed_on: date
    per_share_basis: NonEmptyString
    source_id: Identifier
    quote_excerpt: NonEmptyString


class WebConclusion(InvestmentConclusionV1):
    """Objective conclusions preserve the provenance of Web-backed assumptions."""

    scenarios: tuple[WebScenario, ...] = Field(strict=False, min_length=3, max_length=3)


class WebMainBody(ConfiguredMainBody):
    """The main agent can resolve gaps through its own research while preserving limitations."""

    research: IndependentResearch
    web_observed_price: WebPriceObservation | None
    conclusions: tuple[WebConclusion, ...] = Field(strict=False, min_length=2, max_length=2)


class WebReviewBody(ConfiguredReviewBody):
    """A reviewer can independently search for support and counterevidence."""

    research: IndependentResearch


class WebAnalysisV1(StrictContractModel):
    """One independently researched role artifact with exact initial-input provenance."""

    schema_id: Literal["detailed-analysis.web-analysis"]
    schema_version: Literal[1]
    task_id: Identifier
    artifact_id: Identifier
    role: Literal[
        "track_a_capital",
        "track_a_income",
        "track_b_capital",
        "track_b_income",
        "track_a_aggregator",
        "track_b_aggregator",
        "orchestrator",
        "primary_reviewer",
    ]
    created_at: Timestamp
    agent_alias: Identifier
    agent: AgentDefinition
    configuration_reference: ArtifactReferenceV1
    initial_evidence_set_reference: ArtifactReferenceV1
    input_artifact_references: tuple[ArtifactReferenceV1, ...] = Field(strict=False)
    payload: WebSpecialistBody | WebAggregateBody | WebMainBody | WebReviewBody

    @model_validator(mode="after")
    def validate_role(self) -> WebAnalysisV1:
        """Bind the payload type and explicitly distinguish initial verified evidence from Web reports."""
        expected = (
            WebSpecialistBody
            if self.role.endswith(("capital", "income"))
            else WebAggregateBody
            if self.role.endswith("aggregator")
            else WebMainBody
            if self.role == "orchestrator"
            else WebReviewBody
        )
        if not isinstance(self.payload, expected):
            raise ValueError("web_role_payload_invalid")
        if self.configuration_reference.schema_id != "detailed-analysis.agent-configuration":
            raise ValueError("web_configuration_reference_invalid")
        if self.initial_evidence_set_reference.schema_id != "detailed-analysis.evidence-set":
            raise ValueError("web_initial_evidence_reference_invalid")
        return self
