"""Pure source adapter boundary without transport or credential ownership."""

import hashlib
from typing import Protocol, TypeVar

from pydantic import Field, field_validator, model_validator

from stock_research_llm_orchestrator.contracts.base import Identifier, Sha256Hex, StrictContractModel
from stock_research_llm_orchestrator.requests.transport import PhysicalTransportRequest, TemporaryRawCandidate


_FORBIDDEN_PARAMETER_TERMS = (
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "credential",
    "password",
    "secret",
    "token",
)


class SourceParameter(StrictContractModel):
    """One canonical, non-secret source parameter."""

    name: Identifier
    value: str = Field(min_length=1, max_length=512)

    @field_validator("name")
    @classmethod
    def reject_credential_parameter_names(cls, value: str) -> str:
        """Keep credential-bearing names outside persisted source intent."""
        normalized = value.lower().replace("-", "_").replace(".", "_")
        if any(term in normalized for term in _FORBIDDEN_PARAMETER_TERMS):
            raise ValueError("credential_parameter_forbidden")
        return value


class CredentialFreeSourceIntent(StrictContractModel):
    """Canonical source request description safe for durable persistence."""

    source_id: Identifier
    operation: Identifier
    origin: Identifier
    resource_key: Identifier
    parameters: tuple[SourceParameter, ...] = ()

    @model_validator(mode="after")
    def require_canonical_parameter_order(self) -> CredentialFreeSourceIntent:
        """Reject duplicate or unstable parameter representations."""
        names = tuple(parameter.name for parameter in self.parameters)
        if names != tuple(sorted(names)) or len(names) != len(set(names)):
            raise ValueError("source_parameters_not_canonical")
        return self

    def to_transport_request(self, logical_request_id: str, physical_attempt_id: str) -> PhysicalTransportRequest:
        """Drop parser-only parameters at the controlled transport boundary."""
        return PhysicalTransportRequest(
            logical_request_id=logical_request_id,
            physical_attempt_id=physical_attempt_id,
            origin=self.origin,
            operation=self.operation,
            resource_key=self.resource_key,
        )


class BoundedSourceResponse(StrictContractModel):
    """Exact transport-validated bytes supplied to a source-native parser."""

    physical_attempt_id: Identifier
    body: bytes = Field(repr=False)
    sha256: Sha256Hex
    media_type: str = Field(min_length=1, max_length=128)
    encoding: str = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_exact_hash(self) -> BoundedSourceResponse:
        """Reject bytes that differ from the transport candidate receipt."""
        if hashlib.sha256(self.body).hexdigest() != self.sha256:
            raise ValueError("source_candidate_hash_mismatch")
        return self

    @classmethod
    def from_candidate(cls, candidate: TemporaryRawCandidate) -> BoundedSourceResponse:
        """Recheck exact-byte identity while crossing into source parsing."""
        return cls(
            physical_attempt_id=candidate.physical_attempt_id,
            body=candidate.body,
            sha256=candidate.sha256,
            media_type=candidate.media_type,
            encoding=candidate.encoding,
        )


SourceNativeModel = TypeVar("SourceNativeModel", bound=StrictContractModel, covariant=True)


class SourceAdapter(Protocol[SourceNativeModel]):
    """Pure adapter that owns intent construction and source-native parsing only."""

    @property
    def source_id(self) -> str:
        """Return the immutable source identifier implemented by this adapter."""
        ...

    def build_intent(self, operation: str, parameters: tuple[SourceParameter, ...]) -> CredentialFreeSourceIntent:
        """Build one canonical credential-free source intent."""
        ...

    def parse(self, response: BoundedSourceResponse) -> SourceNativeModel:
        """Parse bounded exact bytes without network, retry, sleep, or fallback."""
        ...
