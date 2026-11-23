"""Role-to-agent bindings independent of model branding and runtime protocols."""

from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from stock_research_llm_orchestrator.contracts.base import Identifier, StrictContractModel


type ModelId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,199}$")]


class ReasoningSettings(StrictContractModel):
    """Requested effort; unsupported settings never trigger a lower-effort fallback."""

    effort: Literal["minimal", "low", "medium", "high", "xhigh", "max", "ultra"]


class AgentDefinition(StrictContractModel):
    """One implemented transport with an explicit model and declared family."""

    adapter: Literal["codex_cli", "claude_cli"]
    model: ModelId
    model_family: Identifier
    reasoning: ReasoningSettings

    @model_validator(mode="after")
    def validate_effort(self) -> AgentDefinition:
        """Reject settings unsupported by the implemented CLI mappings."""
        allowed = (
            {"minimal", "low", "medium", "high", "xhigh", "max", "ultra"}
            if self.adapter == "codex_cli"
            else {"low", "medium", "high", "max"}
        )
        if self.reasoning.effort not in allowed:
            raise ValueError("agent_effort_unsupported")
        return self


class TrackRoles(StrictContractModel):
    """Two objectives and a separate aggregation session."""

    capital: Identifier
    income: Identifier
    aggregator: Identifier


class RoleBindings(StrictContractModel):
    """Stable workflow roles; assigning the same agent never shares a session."""

    track_a: TrackRoles
    track_b: TrackRoles
    orchestrator: Identifier
    primary_reviewer: Identifier
    translation: Identifier

    def assignments(self) -> dict[str, str]:
        """Resolve the fixed execution order without encoding a provider into role names."""
        return {
            "track_a_capital": self.track_a.capital,
            "track_a_income": self.track_a.income,
            "track_b_capital": self.track_b.capital,
            "track_b_income": self.track_b.income,
            "track_a_aggregator": self.track_a.aggregator,
            "track_b_aggregator": self.track_b.aggregator,
            "orchestrator": self.orchestrator,
            "primary_reviewer": self.primary_reviewer,
            "translation": self.translation,
        }


class ReviewPolicy(StrictContractModel):
    """Explicitly distinguish development self-review from cross-family requirements."""

    mode: Literal["single_family_development", "cross_family_required"]


class AgentConfigurationV1(StrictContractModel):
    """Versioned, credential-free personal execution profile."""

    schema_id: Literal["detailed-analysis.agent-configuration"]
    schema_version: Literal[1]
    artifact_id: Identifier
    agents: dict[Identifier, AgentDefinition] = Field(min_length=1)
    roles: RoleBindings
    review_policy: ReviewPolicy

    @model_validator(mode="after")
    def validate_bindings(self) -> AgentConfigurationV1:
        """Reject missing agents and unsatisfied declared-family independence."""
        assigned = self.roles.assignments()
        if not set(assigned.values()) <= self.agents.keys():
            raise ValueError("agent_reference_missing")
        if self.review_policy.mode == "cross_family_required":
            family_a = {self.agents[assigned[role]].model_family for role in assigned if role.startswith("track_a_")}
            family_b = {self.agents[assigned[role]].model_family for role in assigned if role.startswith("track_b_")}
            main = self.agents[self.roles.orchestrator].model_family
            reviewer = self.agents[self.roles.primary_reviewer].model_family
            if family_a & family_b or main == reviewer:
                raise ValueError("agent_cross_family_requirement_unsatisfied")
            # The same transport/model cannot become independent by changing its declared family.
            model_a = {
                (self.agents[assigned[r]].adapter, self.agents[assigned[r]].model)
                for r in assigned
                if r.startswith("track_a_")
            }
            model_b = {
                (self.agents[assigned[r]].adapter, self.agents[assigned[r]].model)
                for r in assigned
                if r.startswith("track_b_")
            }
            main_agent = self.agents[self.roles.orchestrator]
            review_agent = self.agents[self.roles.primary_reviewer]
            if model_a & model_b or (main_agent.adapter, main_agent.model) == (
                review_agent.adapter,
                review_agent.model,
            ):
                raise ValueError("agent_cross_family_requirement_unsatisfied")
        return self
