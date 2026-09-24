"""Credential-safe EDINET physical transport boundary."""

from collections.abc import Callable
from dataclasses import dataclass, field
from os import PathLike
from typing import Annotated, Literal

from pydantic import StringConstraints

from stock_research_llm_orchestrator.contracts.base import Identifier, StrictContractModel
from stock_research_llm_orchestrator.credentials.edinet import CredentialAccessError, use_edinet_api_key_for_send
from stock_research_llm_orchestrator.credentials.models import CredentialFilePolicy
from stock_research_llm_orchestrator.credentials.preflight import preflight_credential_file
from stock_research_llm_orchestrator.requests.transport import PhysicalTransportRequest, UntrustedTransportResponse
from stock_research_llm_orchestrator.sources.protocol import CredentialFreeSourceIntent, SourceParameter


_EDINET_ORIGIN = "api.edinet-fsa.go.jp"
_OWNER_ONLY_POLICY = CredentialFilePolicy(required_mode=0o600)
_HttpsPath = Annotated[str, StringConstraints(pattern=r"^/[A-Za-z0-9./_-]+$", max_length=1024)]


class EdinetTransportError(RuntimeError):
    """Sanitized failure before or during an EDINET physical send."""


class EdinetHttpTarget(StrictContractModel):
    """Credential-free EDINET HTTP fields safe for diagnostics and tests."""

    method: Literal["GET"] = "GET"
    scheme: Literal["https"] = "https"
    origin: Identifier
    path: _HttpsPath
    query: tuple[SourceParameter, ...]


EdinetWireSend = Callable[[EdinetHttpTarget, str], UntrustedTransportResponse]


def render_edinet_http_target(intent: CredentialFreeSourceIntent) -> EdinetHttpTarget:
    """Render an approved credential-free intent without adding its API key."""
    if intent.source_id != "edinet" or intent.origin != _EDINET_ORIGIN:
        raise EdinetTransportError("edinet_intent_not_approved")
    values = {parameter.name: parameter.value for parameter in intent.parameters}
    names = tuple(parameter.name for parameter in intent.parameters)
    if (
        intent.operation == "document-list"
        and intent.resource_key == "api-v2-documents"
        and names == ("date", "type")
        and values.get("type") == "2"
    ):
        path = "/api/v2/documents.json"
        query = intent.parameters
    elif (
        intent.operation == "document-retrieval"
        and intent.resource_key == values.get("document_id")
        and names == ("document_id", "type")
        and values.get("type") == "1"
    ):
        path = f"/api/v2/documents/{intent.resource_key}"
        query = (SourceParameter(name="type", value="1"),)
    else:
        raise EdinetTransportError("edinet_intent_not_approved")
    return EdinetHttpTarget(origin=intent.origin, path=path, query=query)


@dataclass(frozen=True)
class EdinetPhysicalTransport:
    """Preflight and read one mounted key only at an injected physical send."""

    intent: CredentialFreeSourceIntent
    credential_path: str | PathLike[str] = field(repr=False)
    wire_send: EdinetWireSend = field(repr=False)

    def __call__(self, request: PhysicalTransportRequest) -> UntrustedTransportResponse:
        """Validate identity, preflight metadata, then perform exactly one send."""
        expected = self.intent.to_transport_request(request.logical_request_id, request.physical_attempt_id)
        if request != expected:
            raise EdinetTransportError("edinet_transport_request_mismatch")
        target = render_edinet_http_target(self.intent)
        preflight = preflight_credential_file(self.credential_path, policy=_OWNER_ONLY_POLICY)
        if preflight.status != "ready":
            raise EdinetTransportError(f"edinet_credential_{preflight.reason_code}")
        try:
            return use_edinet_api_key_for_send(
                self.credential_path,
                lambda subscription_key: self.wire_send(target, subscription_key),
            )
        except CredentialAccessError:
            raise EdinetTransportError("edinet_physical_send_failed") from None
