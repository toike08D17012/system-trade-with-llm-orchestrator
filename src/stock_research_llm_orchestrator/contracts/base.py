"""Shared primitives for immutable strict contracts."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


NonEmptyString = Annotated[str, StringConstraints(min_length=1)]
Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")]
SchemaIdentifier = Annotated[str, StringConstraints(pattern=r"^detailed-analysis\.[a-z0-9]+(?:-[a-z0-9]+)*$")]
Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
Timestamp = Annotated[
    str,
    StringConstraints(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$"),
]


class StrictContractModel(BaseModel):
    """Base class that rejects coercion and unknown fields."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ArtifactReferenceV1(StrictContractModel):
    """Immutable reference to an exact artifact and contract version."""

    artifact_type: NonEmptyString
    artifact_id: Identifier
    schema_id: SchemaIdentifier
    schema_version: int = Field(ge=1)
    sha256: Sha256Hex
