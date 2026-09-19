"""Applied instruction-set provenance and deterministic composition for version 1."""

import hashlib
from typing import Literal

from pydantic import Field, field_validator, model_validator

from stock_research_llm_orchestrator.contracts.base import (
    ArtifactReferenceV1,
    FileReferenceV1,
    Identifier,
    NonEmptyString,
    Sha256Hex,
    StrictContractModel,
    Timestamp,
)
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.evidence import _parse_rfc3339
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.research import AgentRoleValue


class InstructionComponentV1(StrictContractModel):
    """One immutable instruction component in deterministic composition order."""

    order: int = Field(ge=1)
    component_type: Literal["common", "role", "task_scoped"]
    instruction_id: Identifier
    instruction_version: int = Field(ge=1)
    source_reference: FileReferenceV1
    content_sha256: Sha256Hex

    @model_validator(mode="after")
    def validate_source_hash(self) -> InstructionComponentV1:
        """Bind the applied content hash to the exact referenced source bytes."""
        if self.source_reference.sha256 != self.content_sha256:
            raise ValueError("instruction component source and content hashes must match")
        return self


class InstructionSourceDefinitionV1(StrictContractModel):
    """One repository-managed instruction source in the static set definition."""

    instruction_id: Identifier
    instruction_version: int = Field(ge=1)
    relative_path: NonEmptyString
    content_sha256: Sha256Hex


class RoleInstructionDefinitionV1(InstructionSourceDefinitionV1):
    """Role source plus the allowed output and least-privilege profiles."""

    output_schema_ids: tuple[NonEmptyString, ...] = Field(strict=False, min_length=1)
    permission_profile: Identifier
    workspace_profile: Identifier


class TaskScopedInstructionDefinitionV1(StrictContractModel):
    """Rules for the dynamic final component applied to each Agent run."""

    required: Literal[True]
    content_sha256_recorded_at_application: Literal[True]
    contains_validated_task_and_artifact_references_only: Literal[True]


class InstructionSetDefinitionV1(StrictContractModel):
    """Static versioned definition for deterministic role instruction composition."""

    instruction_set_id: Identifier
    instruction_set_version: int = Field(ge=1)
    composition_order: tuple[Literal["common", "role", "task_scoped"], ...] = Field(
        strict=False, min_length=3, max_length=3
    )
    common: InstructionSourceDefinitionV1
    roles: dict[str, RoleInstructionDefinitionV1]
    task_scoped: TaskScopedInstructionDefinitionV1
    applied_record_schema_id: Literal["detailed-analysis.instruction-application"]
    physical_distribution_implemented: Literal[False]

    @model_validator(mode="after")
    def validate_instruction_set(self) -> InstructionSetDefinitionV1:
        """Require the five approved roles and exact three-part composition order."""
        if self.composition_order != ("common", "role", "task_scoped"):
            raise ValueError("instruction-set composition order must be common, role, then task_scoped")
        expected_roles = {
            "orchestrator",
            "codex_worker",
            "claude_worker",
            "primary_reviewer",
            "antigravity_auditor",
        }
        if set(self.roles) != expected_roles:
            raise ValueError("instruction set must define exactly the five approved roles")
        if len({source.instruction_id for source in self.roles.values()}) != len(self.roles):
            raise ValueError("role instruction IDs must be unique")
        return self


class InstructionApplicationV1(StrictContractModel):
    """Exact instructions, settings, permissions, and schema applied to one run."""

    schema_id: Literal["detailed-analysis.instruction-application"]
    schema_version: Literal[1]
    task_id: Identifier
    artifact_id: Identifier
    agent_run_id: Identifier
    logical_session_id: Identifier
    role: AgentRoleValue
    applied_at: Timestamp
    instruction_set_id: Identifier
    instruction_set_version: int = Field(ge=1)
    components: tuple[InstructionComponentV1, ...] = Field(strict=False, min_length=3, max_length=3)
    composed_instruction_sha256: Sha256Hex
    model: NonEmptyString
    effort: NonEmptyString
    permission_profile: Identifier
    workspace_profile: Identifier
    policy_references: tuple[ArtifactReferenceV1, ...] = Field(strict=False, min_length=1)
    output_schema_id: NonEmptyString
    output_schema_version: int = Field(ge=1)
    output_schema_sha256: Sha256Hex
    external_content_separated_from_instructions: Literal[True]
    external_content_treated_as_untrusted_data: Literal[True]
    secret_values_included: Literal[False]

    @field_validator("applied_at")
    @classmethod
    def ensure_valid_applied_at(cls, value: str) -> str:
        """Validate the instruction application timestamp."""
        _parse_rfc3339(value, "applied_at")
        return value

    @model_validator(mode="after")
    def validate_composition(self) -> InstructionApplicationV1:
        """Require common-first, role-second, deterministic component composition."""
        orders = tuple(component.order for component in self.components)
        if orders != tuple(range(1, len(self.components) + 1)):
            raise ValueError("instruction component order must be contiguous and start at one")
        component_types = tuple(component.component_type for component in self.components)
        if component_types != ("common", "role", "task_scoped"):
            raise ValueError("instruction composition must be common, role, then task_scoped")
        component_ids = [component.instruction_id for component in self.components]
        if len(component_ids) != len(set(component_ids)):
            raise ValueError("instruction component IDs must be unique")
        return self


def compose_instruction_text(common_text: str, role_text: str, task_scoped_text: str) -> str:
    """Compose three exact instruction components without ambient or filesystem input."""
    components = (common_text, role_text, task_scoped_text)
    for component in components:
        if "\r" in component or not component.endswith("\n"):
            raise ValueError("instruction components must use LF newlines and end with one newline")
    return "\n---\n\n".join(components)


def instruction_text_sha256(content: str) -> str:
    """Hash exact UTF-8 instruction content for application provenance."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
