"""Standard acquisition round trips and immutable role evidence snapshots."""

from datetime import date, datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, model_validator

from stock_research_llm_orchestrator.contracts.base import (
    ArtifactReferenceV1,
    FileReferenceV1,
    Identifier,
    NonEmptyString,
    Sha256Hex,
    StrictContractModel,
    Timestamp,
)
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.agent_configuration import AgentDefinition
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.web_analysis import (
    WebAggregateBody,
    WebMainBody,
    WebReviewBody,
    WebSpecialistBody,
)


type AcquisitionValidation = Literal["pending", "verified", "rejected"]
type AcquisitionRole = Literal[
    "track_a_capital",
    "track_a_income",
    "track_b_capital",
    "track_b_income",
    "track_a_aggregator",
    "track_b_aggregator",
    "orchestrator",
    "primary_reviewer",
]


class AcquisitionRequest(StrictContractModel):
    """A model-selected operation, with caller identity supplied separately by runtime."""

    operation: Literal["listing", "market", "public_document"]
    security_code: NonEmptyString
    purpose: NonEmptyString
    url: str | None = None
    start: date | None = None
    end: date | None = None
    classification: Literal["issuer-ir", "jpx", "public-web"] | None = None

    @model_validator(mode="after")
    def validate_operation(self) -> AcquisitionRequest:
        """Reject ambiguous document targets and inverted market periods."""
        if self.operation == "public_document":
            if not self.url or self.classification is None:
                raise ValueError("acquisition_document_target_required")
            parsed = urlsplit(self.url)
            if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
                raise ValueError("acquisition_document_url_invalid")
            if any(ord(char) < 33 for char in self.url) or any(char in self.url for char in "<>"):
                raise ValueError("acquisition_document_url_invalid")
        elif self.url is not None or self.classification is not None:
            raise ValueError("acquisition_unexpected_document_target")
        if self.operation != "market" and (self.start is not None or self.end is not None):
            raise ValueError("acquisition_unexpected_market_period")
        if self.start is not None and self.end is not None and self.start > self.end:
            raise ValueError("acquisition_market_period_invalid")
        return self


class EvidenceReadRequest(StrictContractModel):
    """Select exact lines or search already acquired evidence without another source request."""

    operation: Literal["search_evidence", "read_evidence"]
    evidence_id: Identifier
    query: str | None = None
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_operation(self) -> EvidenceReadRequest:
        """Keep source search and inclusive line reading unambiguous."""
        if self.operation == "search_evidence":
            if self.query is None or not self.query.strip():
                raise ValueError("evidence_read_search_query_required")
            if self.start_line is not None or self.end_line is not None:
                raise ValueError("evidence_read_search_bounds_invalid")
        else:
            if self.start_line is None or self.end_line is None or self.start_line > self.end_line:
                raise ValueError("evidence_read_range_invalid")
            if self.query is not None:
                raise ValueError("evidence_read_query_unexpected")
        return self


class AcquiredEvidence(StrictContractModel):
    """A program-acquired original or normalized observation, never an agent Web claim."""

    evidence_id: Identifier
    source_id: Literal["jpx", "yfinance", "issuer-ir", "public-web"]
    source_reference: NonEmptyString
    retrieved_at: Timestamp
    raw_reference: FileReferenceV1
    text: NonEmptyString
    validation: AcquisitionValidation = "verified"


class AcquisitionResult(StrictContractModel):
    """An exact request outcome with physical attempts and immutable file references."""

    schema_id: Literal["detailed-analysis.acquisition-result"] = "detailed-analysis.acquisition-result"
    schema_version: Literal[1] = 1
    request_sha256: Sha256Hex
    status: Literal["not_attempted", "waiting", "failed", "acquired"]
    validation: AcquisitionValidation
    requested_at: Timestamp
    completed_at: Timestamp
    attempt_references: tuple[FileReferenceV1, ...] = Field(default=(), strict=False)
    files: tuple[FileReferenceV1, ...] = Field(default=(), strict=False)
    evidence: tuple[AcquiredEvidence, ...] = Field(default=(), strict=False)
    reuse_reference: FileReferenceV1 | None = None
    detail: NonEmptyString

    @model_validator(mode="after")
    def validate_outcome(self) -> AcquisitionResult:
        """Prevent failed or unattempted operations from claiming acquired evidence."""
        requested = datetime.fromisoformat(self.requested_at)
        completed = datetime.fromisoformat(self.completed_at)
        if requested > completed or any(
            datetime.fromisoformat(item.retrieved_at) > completed for item in self.evidence
        ):
            raise ValueError("acquisition_timestamp_order_invalid")
        if self.status == "acquired":
            if not self.evidence or not self.files or (not self.attempt_references and self.reuse_reference is None):
                raise ValueError("acquisition_original_or_attempt_missing")
        elif self.evidence or self.validation == "verified":
            raise ValueError("acquisition_unavailable_evidence_invalid")
        if self.validation == "verified" and any(item.validation != "verified" for item in self.evidence):
            raise ValueError("acquisition_aggregate_validation_mismatch")
        if self.status == "failed" and not self.attempt_references:
            raise ValueError("acquisition_failure_without_attempt")
        if self.status == "not_attempted" and self.attempt_references:
            raise ValueError("acquisition_unattempted_with_attempt")
        ids = [item.evidence_id for item in self.evidence]
        if len(ids) != len(set(ids)):
            raise ValueError("acquisition_duplicate_evidence")
        available = {(item.relative_path, item.sha256) for item in self.files}
        if any(
            (item.raw_reference.relative_path, item.raw_reference.sha256) not in available for item in self.evidence
        ):
            raise ValueError("acquisition_original_not_registered")
        return self


class AcquisitionSnapshotV1(StrictContractModel):
    """The exact initial and additional evidence visible to a role at a given turn."""

    schema_id: Literal["detailed-analysis.acquisition-snapshot"]
    schema_version: Literal[1]
    task_id: Identifier
    artifact_id: Identifier
    created_at: Timestamp
    version: int = Field(ge=1)
    initial_evidence_set_reference: ArtifactReferenceV1
    evidence: tuple[AcquiredEvidence, ...] = Field(strict=False)

    @model_validator(mode="after")
    def validate_evidence(self) -> AcquisitionSnapshotV1:
        """Bind the original evidence set and reject duplicate additional identities."""
        if self.initial_evidence_set_reference.schema_id != "detailed-analysis.evidence-set":
            raise ValueError("acquisition_initial_evidence_reference_invalid")
        ids = [item.evidence_id for item in self.evidence]
        if len(ids) != len(set(ids)):
            raise ValueError("acquisition_snapshot_duplicate_evidence")
        return self


class AcquisitionTurn(StrictContractModel):
    """A resumable role response containing acquisition requests or a final payload."""

    kind: Literal["acquire", "final"]
    requests: tuple[AcquisitionRequest | EvidenceReadRequest, ...] = Field(strict=False)
    state: str = ""
    payload: WebSpecialistBody | WebAggregateBody | WebMainBody | WebReviewBody | None

    @model_validator(mode="after")
    def validate_turn(self) -> AcquisitionTurn:
        """Require an unambiguous acquisition or analysis response."""
        if self.kind == "acquire" and (not self.requests or self.payload is not None):
            raise ValueError("acquisition_turn_requests_required")
        if self.kind == "final" and (self.requests or self.payload is None):
            raise ValueError("acquisition_turn_final_payload_required")
        return self


class AcquisitionSpecialistTurn(AcquisitionTurn):
    """One objective specialist's resumable response."""

    payload: WebSpecialistBody | None


class AcquisitionAggregateTurn(AcquisitionTurn):
    """One track organizer's resumable response."""

    payload: WebAggregateBody | None


class AcquisitionMainTurn(AcquisitionTurn):
    """The main synthesis agent's resumable response."""

    payload: WebMainBody | None


class AcquisitionReviewTurn(AcquisitionTurn):
    """The primary reviewer's resumable response."""

    payload: WebReviewBody | None


class AcquisitionAnalysisV1(StrictContractModel):
    """A role analysis bound to the snapshot actually supplied to that role."""

    schema_id: Literal["detailed-analysis.acquisition-analysis"]
    schema_version: Literal[1]
    task_id: Identifier
    artifact_id: Identifier
    role: AcquisitionRole
    created_at: Timestamp
    agent_alias: Identifier
    agent: AgentDefinition
    configuration_reference: ArtifactReferenceV1
    evidence_snapshot_reference: ArtifactReferenceV1
    input_artifact_references: tuple[ArtifactReferenceV1, ...] = Field(strict=False)
    payload: WebSpecialistBody | WebAggregateBody | WebMainBody | WebReviewBody

    @model_validator(mode="after")
    def validate_role(self) -> AcquisitionAnalysisV1:
        """Keep role-specific payloads and the immutable input references explicit."""
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
            raise ValueError("acquisition_role_payload_invalid")
        if self.configuration_reference.schema_id != "detailed-analysis.agent-configuration":
            raise ValueError("acquisition_configuration_reference_invalid")
        if self.evidence_snapshot_reference.schema_id != "detailed-analysis.acquisition-snapshot":
            raise ValueError("acquisition_snapshot_reference_invalid")
        return self
