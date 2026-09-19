"""Evidence provenance and lifecycle contracts for detailed analysis version 1."""

from datetime import date, datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, JsonValue, field_validator, model_validator

from stock_research_llm_orchestrator.contracts.base import (
    ArtifactReferenceV1,
    Identifier,
    NonEmptyString,
    Sha256Hex,
    StrictContractModel,
    Timestamp,
)


class EvidenceLayer(StrEnum):
    """Persisted evidence layers that must remain distinguishable."""

    SOURCE_METADATA = "source_metadata"
    RAW = "raw"
    NORMALIZED = "normalized"
    CALCULATED = "calculated"


class EvidenceSetState(StrEnum):
    """Lifecycle state of an immutable evidence-set version."""

    FROZEN = "frozen"
    ANALYSIS_COMPLETED = "analysis_completed"
    INVALIDATED = "invalidated"


class FreshnessStatus(StrEnum):
    """Result of checking one required evidence category."""

    CURRENT = "current"
    STALE = "stale"
    MISSING = "missing"
    CONFLICTING = "conflicting"


EvidenceLayerValue = Annotated[EvidenceLayer, Field(strict=False)]
FreshnessStatusValue = Annotated[FreshnessStatus, Field(strict=False)]


def _parse_rfc3339(value: str, field_name: str) -> datetime:
    """Parse an offset-bearing RFC 3339 timestamp for ordering checks."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be a valid RFC 3339 timestamp") from exc
    if parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must include a UTC offset")
    return parsed


class ApplicablePeriodV1(StrictContractModel):
    """Inclusive calendar period represented by a piece of evidence."""

    start_date: str
    end_date: str

    @model_validator(mode="after")
    def ensure_valid_period(self) -> ApplicablePeriodV1:
        """Require ISO dates in chronological order."""
        try:
            start = date.fromisoformat(self.start_date)
            end = date.fromisoformat(self.end_date)
        except ValueError as exc:
            raise ValueError("applicable period dates must use ISO 8601 YYYY-MM-DD") from exc
        if end < start:
            raise ValueError("applicable period end_date must not precede start_date")
        return self


class SourceMetadataEvidenceV1(StrictContractModel):
    """Source and acquisition metadata stored separately from raw content."""

    evidence_id: Identifier
    layer: Literal["source_metadata"]
    source_id: Identifier
    source_approval_reference: ArtifactReferenceV1
    provider: NonEmptyString
    service: NonEmptyString
    dataset: NonEmptyString
    published_at: Timestamp | None
    first_available_at: Timestamp | None
    updated_at: Timestamp | None
    retrieved_at: Timestamp
    applicable_period: ApplicablePeriodV1
    timezone: NonEmptyString
    reference: NonEmptyString
    content_sha256: Sha256Hex
    data_version: NonEmptyString

    @field_validator("published_at", "first_available_at", "updated_at", "retrieved_at")
    @classmethod
    def ensure_valid_timestamps(cls, value: str | None, info: object) -> str | None:
        """Reject timestamps that only match the surface pattern."""
        if value is not None:
            field_name = getattr(info, "field_name", "timestamp")
            _parse_rfc3339(value, field_name)
        return value

    @model_validator(mode="after")
    def ensure_source_time_order(self) -> SourceMetadataEvidenceV1:
        """Reject metadata that claims retrieval before public availability."""
        retrieved = _parse_rfc3339(self.retrieved_at, "retrieved_at")
        for field_name, value in (
            ("published_at", self.published_at),
            ("first_available_at", self.first_available_at),
            ("updated_at", self.updated_at),
        ):
            if value is not None and _parse_rfc3339(value, field_name) > retrieved:
                raise ValueError(f"{field_name} must not be later than retrieved_at")
        return self


class RawEvidenceV1(StrictContractModel):
    """Exact raw payload stored without source metadata duplication."""

    evidence_id: Identifier
    layer: Literal["raw"]
    source_metadata_evidence_id: Identifier
    payload_reference: ArtifactReferenceV1
    content_sha256: Sha256Hex
    data_version: NonEmptyString

    @model_validator(mode="after")
    def ensure_payload_hash_matches(self) -> RawEvidenceV1:
        """Bind the raw evidence record to the exact stored payload."""
        if self.content_sha256 != self.payload_reference.sha256:
            raise ValueError("raw evidence hash must match its payload reference")
        return self


class DerivedEvidenceV1(StrictContractModel):
    """Fields shared by normalized and calculated evidence."""

    evidence_id: Identifier
    input_evidence_ids: tuple[Identifier, ...] = Field(strict=False, min_length=1)
    value: JsonValue
    missing_reason: NonEmptyString | None
    unit: NonEmptyString
    content_sha256: Sha256Hex
    data_version: NonEmptyString

    @field_validator("input_evidence_ids")
    @classmethod
    def ensure_unique_inputs(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        """Reject repeated provenance references."""
        if len(values) != len(set(values)):
            raise ValueError("input evidence IDs must be unique")
        return values

    @model_validator(mode="after")
    def ensure_missingness_is_explicit(self) -> DerivedEvidenceV1:
        """Keep a missing value distinct from a numeric or textual zero."""
        if self.value is None and self.missing_reason is None:
            raise ValueError("missing_reason is required when value is null")
        if self.value is not None and self.missing_reason is not None:
            raise ValueError("missing_reason must be null when value is present")
        return self


class NormalizedEvidenceV1(DerivedEvidenceV1):
    """Deterministically normalized evidence and its source references."""

    layer: Literal["normalized"]
    normalization_logic_version: int = Field(ge=1)


class CalculatedEvidenceV1(DerivedEvidenceV1):
    """Deterministically calculated metric with reproducible provenance."""

    layer: Literal["calculated"]
    calculation_logic_version: int = Field(ge=1)
    rounding_method: NonEmptyString


EvidenceRecordV1 = Annotated[
    SourceMetadataEvidenceV1 | RawEvidenceV1 | NormalizedEvidenceV1 | CalculatedEvidenceV1,
    Field(discriminator="layer"),
]


class FreshnessAssessmentV1(StrictContractModel):
    """Freshness result for one required evidence category."""

    category: Identifier
    status: FreshnessStatusValue
    evidence_ids: tuple[Identifier, ...] = Field(strict=False)
    requirement: NonEmptyString
    detail: NonEmptyString

    @field_validator("evidence_ids")
    @classmethod
    def ensure_unique_evidence_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        """Reject repeated freshness references."""
        if len(values) != len(set(values)):
            raise ValueError("freshness evidence IDs must be unique")
        return values


class EvidenceInvalidationV1(StrictContractModel):
    """Material evidence update and the exact artifacts it invalidates."""

    invalidation_id: Identifier
    created_at: Timestamp
    triggering_evidence_ids: tuple[Identifier, ...] = Field(strict=False, min_length=1)
    affected_artifact_ids: tuple[Identifier, ...] = Field(strict=False, min_length=1)
    reason: NonEmptyString

    @field_validator("created_at")
    @classmethod
    def ensure_valid_created_at(cls, value: str) -> str:
        """Validate the invalidation timestamp."""
        _parse_rfc3339(value, "created_at")
        return value

    @field_validator("triggering_evidence_ids", "affected_artifact_ids")
    @classmethod
    def ensure_unique_identifiers(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        """Reject duplicated invalidation targets."""
        if len(values) != len(set(values)):
            raise ValueError("invalidation identifiers must be unique")
        return values


class PreviousEvidenceSetReferenceV1(StrictContractModel):
    """Reference to the exact immediately preceding evidence-set version."""

    evidence_set_version: int = Field(ge=1)
    artifact_reference: ArtifactReferenceV1


class EvidenceSetV1(StrictContractModel):
    """Frozen, versioned evidence manifest used by analysis and review."""

    schema_id: Literal["detailed-analysis.evidence-set"]
    schema_version: Literal[1]
    task_id: Identifier
    artifact_id: Identifier
    evidence_set_version: int = Field(ge=1)
    state: EvidenceSetState = Field(strict=False)
    previous_evidence_set: PreviousEvidenceSetReferenceV1 | None
    evidence_frozen_at: Timestamp
    freshness_checked_at: Timestamp
    analysis_completed_at: Timestamp | None
    records: tuple[EvidenceRecordV1, ...] = Field(strict=False, min_length=1)
    freshness_assessments: tuple[FreshnessAssessmentV1, ...] = Field(strict=False, min_length=1)
    invalidations: tuple[EvidenceInvalidationV1, ...] = Field(strict=False)

    @field_validator("evidence_frozen_at", "freshness_checked_at", "analysis_completed_at")
    @classmethod
    def ensure_valid_lifecycle_timestamps(cls, value: str | None, info: object) -> str | None:
        """Reject invalid lifecycle timestamps."""
        if value is not None:
            field_name = getattr(info, "field_name", "timestamp")
            _parse_rfc3339(value, field_name)
        return value

    @model_validator(mode="after")
    def validate_evidence_set(self) -> EvidenceSetV1:
        """Enforce provenance references, monotonic versions, and lifecycle order."""
        records_by_id = {record.evidence_id: record for record in self.records}
        if len(records_by_id) != len(self.records):
            raise ValueError("evidence IDs must be unique within a task evidence set")

        for record in self.records:
            if isinstance(record, RawEvidenceV1):
                source = records_by_id.get(record.source_metadata_evidence_id)
                if not isinstance(source, SourceMetadataEvidenceV1):
                    raise ValueError("raw evidence must reference source_metadata evidence")
            if isinstance(record, DerivedEvidenceV1):
                missing = set(record.input_evidence_ids) - records_by_id.keys()
                if missing:
                    raise ValueError("derived evidence input references must exist in the evidence set")
                if record.evidence_id in record.input_evidence_ids:
                    raise ValueError("derived evidence must not reference itself")

        if self.evidence_set_version == 1:
            if self.previous_evidence_set is not None:
                raise ValueError("evidence set version 1 must not have a previous version")
        elif (
            self.previous_evidence_set is None
            or self.previous_evidence_set.evidence_set_version != self.evidence_set_version - 1
        ):
            raise ValueError("later evidence sets must reference the immediately preceding version")

        frozen_at = _parse_rfc3339(self.evidence_frozen_at, "evidence_frozen_at")
        freshness_at = _parse_rfc3339(self.freshness_checked_at, "freshness_checked_at")
        if freshness_at < frozen_at:
            raise ValueError("freshness_checked_at must not precede evidence_frozen_at")
        if self.analysis_completed_at is not None:
            completed_at = _parse_rfc3339(self.analysis_completed_at, "analysis_completed_at")
            if completed_at < freshness_at:
                raise ValueError("analysis_completed_at must not precede freshness_checked_at")

        if self.state is EvidenceSetState.FROZEN and self.analysis_completed_at is not None:
            raise ValueError("a frozen evidence set must not have analysis_completed_at")
        if self.state is EvidenceSetState.ANALYSIS_COMPLETED and self.analysis_completed_at is None:
            raise ValueError("analysis_completed state requires analysis_completed_at")
        if self.state is EvidenceSetState.INVALIDATED and not self.invalidations:
            raise ValueError("invalidated state requires at least one invalidation")

        known_ids = records_by_id.keys()
        for assessment in self.freshness_assessments:
            if set(assessment.evidence_ids) - known_ids:
                raise ValueError("freshness assessments must reference evidence in this set")
        for invalidation in self.invalidations:
            if set(invalidation.triggering_evidence_ids) - known_ids:
                raise ValueError("invalidations must reference evidence in this set")
            if _parse_rfc3339(invalidation.created_at, "created_at") < frozen_at:
                raise ValueError("invalidation must not precede evidence_frozen_at")
        return self
