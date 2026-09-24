"""Bounded anonymous transport for the official JPX current-list XLSX."""

from dataclasses import dataclass, field

import httpx
from pydantic import Field

from stock_research_llm_orchestrator.contracts.base import StrictContractModel
from stock_research_llm_orchestrator.requests.transport import PhysicalTransportRequest, UntrustedTransportResponse
from stock_research_llm_orchestrator.sources.protocol import CredentialFreeSourceIntent


_PATH = "/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xlsx"


class JpxHttpTransportError(RuntimeError):
    """Sanitized JPX transport failure."""


class JpxHttpClientPolicy(StrictContractModel):
    """Explicit bounds for one JPX XLSX exchange."""

    max_response_bytes: int = Field(ge=1, le=64 * 1024 * 1024)
    connect_timeout_seconds: float = Field(gt=0)
    read_timeout_seconds: float = Field(gt=0)


@dataclass(frozen=True)
class JpxPhysicalTransport:
    """Perform exactly one bounded request for the approved fixed resource."""

    intent: CredentialFreeSourceIntent
    policy: JpxHttpClientPolicy
    transport: httpx.BaseTransport | None = field(default=None, repr=False)

    def __call__(self, request: PhysicalTransportRequest) -> UntrustedTransportResponse:
        """Validate identity and fetch without redirects, retry, or credentials."""
        if request != self.intent.to_transport_request(request.logical_request_id, request.physical_attempt_id):
            raise JpxHttpTransportError("jpx_http_request_mismatch")
        if (
            self.intent.source_id != "jpx"
            or self.intent.operation != "current-listed-issues"
            or self.intent.origin != "www.jpx.co.jp"
            or self.intent.resource_key != "tse-current-listed-issues-xlsx"
            or self.intent.parameters
        ):
            raise JpxHttpTransportError("jpx_http_intent_not_approved")
        try:
            timeout = httpx.Timeout(self.policy.read_timeout_seconds, connect=self.policy.connect_timeout_seconds)
            with (
                httpx.Client(
                    transport=self.transport, timeout=timeout, follow_redirects=False, trust_env=False
                ) as client,
                client.stream("GET", f"https://www.jpx.co.jp{_PATH}") as response,
            ):
                if 300 <= response.status_code < 400:
                    raise JpxHttpTransportError("jpx_http_redirect_rejected")
                body = bytearray()
                for chunk in response.iter_bytes():
                    if len(body) + len(chunk) > self.policy.max_response_bytes:
                        raise JpxHttpTransportError("jpx_http_response_too_large")
                    body.extend(chunk)
                return UntrustedTransportResponse(
                    status_code=response.status_code,
                    body=bytes(body),
                    media_type=response.headers.get("Content-Type", "").partition(";")[0].lower(),
                    encoding="binary",
                    final_origin=response.url.host,
                    redirected=False,
                )
        except JpxHttpTransportError:
            raise
        except Exception:
            raise JpxHttpTransportError("jpx_http_exchange_failed") from None
