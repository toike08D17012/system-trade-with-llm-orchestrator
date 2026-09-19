"""Independent research and common-evidence update contracts for version 1."""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from stock_research_llm_orchestrator.contracts.base import (
    ArtifactReferenceV1,
    Identifier,
    NonEmptyString,
    StrictContractModel,
    Timestamp,
)
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.evidence import _parse_rfc3339
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.task import SecurityInformationV1


class AgentRole(StrEnum):
    """Roles allowed to perform research in the approved workflow."""

    ORCHESTRATOR = "orchestrator"
    CODEX_WORKER = "codex_worker"
    CLAUDE_WORKER = "claude_worker"
    PRIMARY_REVIEWER = "primary_reviewer"
    ANTIGRAVITY_AUDITOR = "antigravity_auditor"


class CandidateRelationship(StrEnum):
    """How a candidate document may affect a proposed claim."""

    SUPPORT = "support"
    COUNTER = "counter"


class CandidateValidationStatus(StrEnum):
    """Validation outcome for a document discovered through search."""

    UNVERIFIED = "unverified"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class DiscoveryOrigin(StrEnum):
    """Origin retained when verified evidence enters the common set."""

    AGENT_SEARCH = "agent_search"
    DETERMINISTIC_ACQUISITION = "deterministic_acquisition"


AgentRoleValue = Annotated[AgentRole, Field(strict=False)]
CandidateRelationshipValue = Annotated[CandidateRelationship, Field(strict=False)]
CandidateValidationStatusValue = Annotated[CandidateValidationStatus, Field(strict=False)]
DiscoveryOriginValue = Annotated[DiscoveryOrigin, Field(strict=False)]


def _ensure_exact_reference(
    reference: ArtifactReferenceV1, *, artifact_type: str, schema_id: str, field_name: str
) -> None:
    """Require a reference to name the intended public artifact contract."""
    if reference.artifact_type != artifact_type or reference.schema_id != schema_id:
        raise ValueError(f"{field_name} must reference {artifact_type} with schema {schema_id}")


class ResearchContextV1(StrictContractModel):
    """Independent worker input containing common evidence but no peer conclusion."""

    schema_id: Literal["detailed-analysis.research-context"]
    schema_version: Literal[1]
    task_id: Identifier
    artifact_id: Identifier
    created_at: Timestamp
    recipient_role: Literal["codex_worker", "claude_worker"]
    task_reference: ArtifactReferenceV1
    evidence_set_reference: ArtifactReferenceV1
    evidence_set_version: int = Field(ge=1)
    security: SecurityInformationV1
    common_evidence_ids: tuple[Identifier, ...] = Field(strict=False, min_length=1)
    source_metadata_evidence_ids: tuple[Identifier, ...] = Field(strict=False, min_length=1)
    research_questions: tuple[NonEmptyString, ...] = Field(strict=False, min_length=1)
    policy_references: tuple[ArtifactReferenceV1, ...] = Field(strict=False, min_length=1)

    @field_validator("created_at")
    @classmethod
    def ensure_valid_created_at(cls, value: str) -> str:
        """Validate context creation time."""
        _parse_rfc3339(value, "created_at")
        return value

    @field_validator("common_evidence_ids", "source_metadata_evidence_ids", "research_questions", "policy_references")
    @classmethod
    def ensure_unique_sequence(cls, values: tuple[object, ...]) -> tuple[object, ...]:
        """Reject duplicate identifiers, questions, or exact policy references."""
        serialized = [repr(value) for value in values]
        if len(serialized) != len(set(serialized)):
            raise ValueError("research context sequences must not contain duplicates")
        return values

    @model_validator(mode="after")
    def validate_context_boundary(self) -> ResearchContextV1:
        """Bind the context to exact task and evidence-set artifacts."""
        _ensure_exact_reference(
            self.task_reference,
            artifact_type="detailed_analysis_task",
            schema_id="detailed-analysis.detailed-analysis-task",
            field_name="task_reference",
        )
        _ensure_exact_reference(
            self.evidence_set_reference,
            artifact_type="evidence_set",
            schema_id="detailed-analysis.evidence-set",
            field_name="evidence_set_reference",
        )
        if not set(self.source_metadata_evidence_ids).issubset(self.common_evidence_ids):
            raise ValueError("source metadata evidence must be part of common evidence")
        return self


class CandidateClaimTargetV1(StrictContractModel):
    """Potential claim that a candidate may support or counter after verification."""

    target_id: Identifier
    relationship: CandidateRelationshipValue
    description: NonEmptyString


class CandidateValidationV1(StrictContractModel):
    """Outcome of fetching and validating an original candidate document."""

    status: CandidateValidationStatusValue
    verified_at: Timestamp | None
    verifier_role: AgentRoleValue | None
    reason: NonEmptyString
    resulting_evidence_ids: tuple[Identifier, ...] = Field(strict=False)

    @field_validator("verified_at")
    @classmethod
    def ensure_valid_verified_at(cls, value: str | None) -> str | None:
        """Validate the optional verification time."""
        if value is not None:
            _parse_rfc3339(value, "verified_at")
        return value

    @model_validator(mode="after")
    def validate_outcome(self) -> CandidateValidationV1:
        """Keep discovery separate from verified evidence adoption."""
        if self.status is CandidateValidationStatus.UNVERIFIED:
            if self.verified_at is not None or self.verifier_role is not None or self.resulting_evidence_ids:
                raise ValueError("unverified candidates must not claim verification or evidence")
        else:
            if self.verified_at is None or self.verifier_role is None:
                raise ValueError("validated candidates require time and verifier role")
        if self.status is CandidateValidationStatus.ACCEPTED and not self.resulting_evidence_ids:
            raise ValueError("accepted candidates require resulting evidence IDs")
        if self.status is CandidateValidationStatus.REJECTED and self.resulting_evidence_ids:
            raise ValueError("rejected candidates must not create evidence IDs")
        return self


class SearchResultV1(StrictContractModel):
    """Untrusted search result retained only as a document candidate."""

    candidate_id: Identifier
    url: NonEmptyString
    headline: NonEmptyString
    snippet: NonEmptyString | None
    claim_targets: tuple[CandidateClaimTargetV1, ...] = Field(strict=False, min_length=1)
    validation: CandidateValidationV1


class SearchRecordV1(StrictContractModel):
    """Agent-specific search record that is not itself evidence."""

    schema_id: Literal["detailed-analysis.search-record"]
    schema_version: Literal[1]
    task_id: Identifier
    artifact_id: Identifier
    agent_run_id: Identifier
    researcher_role: AgentRoleValue
    search_capability: NonEmptyString
    executed_at: Timestamp
    query: NonEmptyString
    results: tuple[SearchResultV1, ...] = Field(strict=False)

    @field_validator("executed_at")
    @classmethod
    def ensure_valid_executed_at(cls, value: str) -> str:
        """Validate the recorded search time."""
        _parse_rfc3339(value, "executed_at")
        return value

    @model_validator(mode="after")
    def ensure_unique_candidates(self) -> SearchRecordV1:
        """Reject duplicate candidate IDs within one search response."""
        candidate_ids = [result.candidate_id for result in self.results]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("candidate IDs must be unique within a search record")
        return self


class EvidenceDiscoveryV1(StrictContractModel):
    """Discovery provenance retained for each newly verified evidence item."""

    evidence_id: Identifier
    origin: DiscoveryOriginValue
    discovered_by_role: AgentRoleValue | None
    search_record_reference: ArtifactReferenceV1 | None
    candidate_id: Identifier | None
    detail: NonEmptyString

    @model_validator(mode="after")
    def validate_discovery_origin(self) -> EvidenceDiscoveryV1:
        """Require search references only for agent-search discoveries."""
        if self.origin is DiscoveryOrigin.AGENT_SEARCH:
            if self.discovered_by_role is None or self.search_record_reference is None or self.candidate_id is None:
                raise ValueError("agent-search discoveries require role, search record, and candidate ID")
            _ensure_exact_reference(
                self.search_record_reference,
                artifact_type="search_record",
                schema_id="detailed-analysis.search-record",
                field_name="search_record_reference",
            )
        elif (
            self.discovered_by_role is not None
            or self.search_record_reference is not None
            or self.candidate_id is not None
        ):
            raise ValueError("deterministic discoveries must not claim an agent-search origin")
        return self


class CommonEvidenceUpdateV1(StrictContractModel):
    """Versioned verified-evidence union distributed equally to both workers."""

    schema_id: Literal["detailed-analysis.common-evidence-update"]
    schema_version: Literal[1]
    task_id: Identifier
    artifact_id: Identifier
    updated_at: Timestamp
    previous_evidence_set_reference: ArtifactReferenceV1
    previous_evidence_set_version: int = Field(ge=1)
    new_evidence_set_reference: ArtifactReferenceV1
    new_evidence_set_version: int = Field(ge=2)
    added_evidence_ids: tuple[Identifier, ...] = Field(strict=False, min_length=1)
    discoveries: tuple[EvidenceDiscoveryV1, ...] = Field(strict=False, min_length=1)
    distributed_to: tuple[Literal["codex_worker", "claude_worker"], ...] = Field(
        strict=False, min_length=2, max_length=2
    )
    invalidated_artifact_ids: tuple[Identifier, ...] = Field(strict=False)

    @field_validator("updated_at")
    @classmethod
    def ensure_valid_updated_at(cls, value: str) -> str:
        """Validate the evidence update time."""
        _parse_rfc3339(value, "updated_at")
        return value

    @model_validator(mode="after")
    def validate_common_update(self) -> CommonEvidenceUpdateV1:
        """Require a one-version step, complete discovery origin, and equal distribution."""
        for field_name, reference in (
            ("previous_evidence_set_reference", self.previous_evidence_set_reference),
            ("new_evidence_set_reference", self.new_evidence_set_reference),
        ):
            _ensure_exact_reference(
                reference,
                artifact_type="evidence_set",
                schema_id="detailed-analysis.evidence-set",
                field_name=field_name,
            )
        if self.new_evidence_set_version != self.previous_evidence_set_version + 1:
            raise ValueError("common evidence updates must advance exactly one evidence-set version")
        if self.distributed_to != ("codex_worker", "claude_worker"):
            raise ValueError("common evidence updates must be distributed to both workers in stable order")
        if len(self.added_evidence_ids) != len(set(self.added_evidence_ids)):
            raise ValueError("added evidence IDs must be unique")
        discovery_ids = [discovery.evidence_id for discovery in self.discoveries]
        if len(discovery_ids) != len(set(discovery_ids)):
            raise ValueError("each added evidence ID must have one discovery record")
        if set(discovery_ids) != set(self.added_evidence_ids):
            raise ValueError("discoveries must cover exactly the added evidence IDs")
        return self
