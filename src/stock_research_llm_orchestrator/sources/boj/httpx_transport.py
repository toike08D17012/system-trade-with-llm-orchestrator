"""Anonymous HTTPX transport for the approved BOJ code API."""

from dataclasses import dataclass, field
from typing import Annotated, Literal

import httpx
from pydantic import Field, StringConstraints

from stock_research_llm_orchestrator.contracts.base import Identifier, StrictContractModel
from stock_research_llm_orchestrator.requests.transport import PhysicalTransportRequest, UntrustedTransportResponse
from stock_research_llm_orchestrator.sources.protocol import CredentialFreeSourceIntent, SourceParameter


_ORIGIN = "www.stat-search.boj.or.jp"
_Path = Annotated[str, StringConstraints(pattern=r"^/[A-Za-z0-9/_-]+$", max_length=256)]


class BojHttpTransportError(RuntimeError):
    """Sanitized BOJ physical transport failure."""


class BojHttpTarget(StrictContractModel):
    """Credential-free BOJ HTTP fields safe for persistence and diagnostics."""

    method: Literal["GET"] = "GET"
    scheme: Literal["https"] = "https"
    origin: Identifier
    path: _Path
    query: tuple[SourceParameter, ...]


class BojHttpClientPolicy(StrictContractModel):
    """Timeout policy for one anonymous BOJ exchange, without a body-size ceiling."""

    max_response_bytes: None = None
    connect_timeout_seconds: float = Field(gt=0, allow_inf_nan=False)
    read_timeout_seconds: float = Field(gt=0, allow_inf_nan=False)
    write_timeout_seconds: float = Field(gt=0, allow_inf_nan=False)
    pool_timeout_seconds: float = Field(gt=0, allow_inf_nan=False)


def render_boj_http_target(intent: CredentialFreeSourceIntent) -> BojHttpTarget:
    """Render only the approved FM08/FXERD04 code API operation."""
    values = {parameter.name: parameter.value for parameter in intent.parameters}
    if (
        intent.source_id != "boj"
        or intent.operation != "fx-daily-code"
        or intent.origin != _ORIGIN
        or intent.resource_key != "api-v1-getDataCode"
        or tuple(values) != ("code", "db", "end_date", "format", "lang", "start_date")
        or values.get("code") != "FXERD04"
        or values.get("db") != "FM08"
        or values.get("format") != "json"
        or values.get("lang") != "en"
    ):
        raise BojHttpTransportError("boj_http_intent_not_approved")
    return BojHttpTarget(
        origin=intent.origin,
        path="/api/v1/getDataCode",
        query=(
            SourceParameter(name="code", value=values["code"]),
            SourceParameter(name="db", value=values["db"]),
            SourceParameter(name="endDate", value=values["end_date"]),
            SourceParameter(name="format", value=values["format"]),
            SourceParameter(name="lang", value=values["lang"]),
            SourceParameter(name="startDate", value=values["start_date"]),
        ),
    )


@dataclass(frozen=True)
class BojPhysicalTransport:
    """Bind one source intent to an anonymous HTTPX send."""

    intent: CredentialFreeSourceIntent
    policy: BojHttpClientPolicy
    transport: httpx.BaseTransport | None = field(default=None, repr=False)

    def __call__(self, request: PhysicalTransportRequest) -> UntrustedTransportResponse:
        """Validate physical identity before performing exactly one request."""
        expected = self.intent.to_transport_request(request.logical_request_id, request.physical_attempt_id)
        if request != expected:
            raise BojHttpTransportError("boj_http_request_mismatch")
        target = render_boj_http_target(self.intent)
        timeout = httpx.Timeout(
            connect=self.policy.connect_timeout_seconds,
            read=self.policy.read_timeout_seconds,
            write=self.policy.write_timeout_seconds,
            pool=self.policy.pool_timeout_seconds,
        )
        try:
            with (
                httpx.Client(
                    transport=self.transport,
                    timeout=timeout,
                    follow_redirects=False,
                    trust_env=False,
                ) as client,
                client.stream(
                    target.method,
                    f"{target.scheme}://{target.origin}{target.path}",
                    params={item.name: item.value for item in target.query},
                    headers={"Accept-Encoding": "gzip"},
                ) as response,
            ):
                if 300 <= response.status_code < 400:
                    raise BojHttpTransportError("boj_http_redirect_rejected")
                body = _read_response(response)
                media_type = response.headers.get("Content-Type", "").partition(";")[0].strip().lower()
                return UntrustedTransportResponse(
                    status_code=response.status_code,
                    body=body,
                    media_type=media_type,
                    encoding="utf-8",
                    final_origin=response.url.host,
                    redirected=False,
                )
        except BojHttpTransportError:
            raise
        except Exception:
            raise BojHttpTransportError("boj_http_exchange_failed") from None


def _read_response(response: httpx.Response) -> bytes:
    body = bytearray()
    for chunk in response.iter_bytes():
        body.extend(chunk)
    return bytes(body)
