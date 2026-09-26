"""Anonymous HTTPX transport for the approved Dukascopy code API."""

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Annotated, Literal

import httpx
from pydantic import Field, StringConstraints

from stock_research_llm_orchestrator.contracts.base import Identifier, StrictContractModel
from stock_research_llm_orchestrator.requests.transport import (
    PhysicalTransportRequest,
    ReceivedResponseValidationError,
    UntrustedTransportResponse,
)
from stock_research_llm_orchestrator.sources.protocol import CredentialFreeSourceIntent, SourceParameter


_ORIGIN = "jetta.dukascopy.com"
_Path = Annotated[str, StringConstraints(pattern=r"^/[A-Za-z0-9/_-]+$", max_length=256)]


class DukascopyHttpTransportError(RuntimeError):
    """Sanitized Dukascopy physical transport failure."""


class _DukascopyResponseValidationError(DukascopyHttpTransportError, ReceivedResponseValidationError):
    """A received response failure with the existing public Dukascopy exception base."""


class DukascopyHttpTarget(StrictContractModel):
    """Credential-free Dukascopy HTTP fields safe for persistence and diagnostics."""

    method: Literal["GET"] = "GET"
    scheme: Literal["https"] = "https"
    origin: Identifier
    path: _Path
    query: tuple[SourceParameter, ...]


class DukascopyHttpClientPolicy(StrictContractModel):
    """Timeout policy for one anonymous Dukascopy exchange, without a body-size ceiling."""

    max_response_bytes: None = None
    connect_timeout_seconds: float = Field(gt=0, allow_inf_nan=False)
    read_timeout_seconds: float = Field(gt=0, allow_inf_nan=False)
    write_timeout_seconds: float = Field(gt=0, allow_inf_nan=False)
    pool_timeout_seconds: float = Field(gt=0, allow_inf_nan=False)


def render_dukascopy_http_target(intent: CredentialFreeSourceIntent) -> DukascopyHttpTarget:
    """Reject anything outside the canonical USDJPY Bid daily year endpoint."""
    import re
    from urllib.parse import parse_qsl, urlsplit

    if (
        intent.source_id != "dukascopy"
        or intent.operation != "fx-daily-bid"
        or intent.origin != _ORIGIN
        or len(intent.parameters) != 1
        or intent.parameters[0].name != "url"
    ):
        raise DukascopyHttpTransportError("dukascopy_intent_invalid")
    url = intent.parameters[0].value
    if not re.fullmatch(r"https://jetta\.dukascopy\.com/v1/candles/day/USD-JPY/BID(?:/[0-9]{4}|\?from=[0-9]+)", url):
        raise DukascopyHttpTransportError("dukascopy_target_invalid")
    parsed = urlsplit(url)
    if parsed.query:
        timestamp = int(dict(parse_qsl(parsed.query))["from"])
        try:
            observed = datetime.fromtimestamp(timestamp / 1000, UTC)
        except ValueError, OverflowError, OSError:
            raise DukascopyHttpTransportError("dukascopy_year_invalid") from None
        year = observed.year
        if observed != datetime(year, 1, 1, tzinfo=UTC):
            raise DukascopyHttpTransportError("dukascopy_year_boundary_invalid")
    else:
        year = int(parsed.path.rsplit("/", 1)[1])
    if year < 2000 or intent.resource_key != f"usdjpy-bid-d1-{year}":
        raise DukascopyHttpTransportError("dukascopy_resource_year_mismatch")
    return DukascopyHttpTarget(
        origin=_ORIGIN,
        path=parsed.path,
        query=tuple(SourceParameter(name=k, value=v) for k, v in parse_qsl(parsed.query)),
    )


@dataclass(frozen=True)
class DukascopyPhysicalTransport:
    """Bind one source intent to an anonymous HTTPX send."""

    intent: CredentialFreeSourceIntent
    policy: DukascopyHttpClientPolicy
    transport: httpx.BaseTransport | None = field(default=None, repr=False)

    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC), repr=False)
    on_received: Callable[[datetime], None] | None = field(default=None, repr=False)

    def __call__(self, request: PhysicalTransportRequest) -> UntrustedTransportResponse:
        """Validate physical identity before performing exactly one request."""
        expected = self.intent.to_transport_request(request.logical_request_id, request.physical_attempt_id)
        if request != expected:
            raise DukascopyHttpTransportError("dukascopy_http_request_mismatch")
        target = render_dukascopy_http_target(self.intent)
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
                    raise _DukascopyResponseValidationError("dukascopy_http_redirect_rejected")
                body = _read_response(response)
                received_at = self.clock()
                if self.on_received is not None:
                    self.on_received(received_at)
                media_type = response.headers.get("Content-Type", "").partition(";")[0].strip().lower()
                return UntrustedTransportResponse(
                    status_code=response.status_code,
                    body=body,
                    media_type=media_type,
                    encoding="utf-8",
                    final_origin=response.url.host,
                    redirected=False,
                    retry_after_seconds=_retry_after(response.headers.get("Retry-After"), received_at),
                )
        except DukascopyHttpTransportError:
            raise
        except Exception:
            raise DukascopyHttpTransportError("dukascopy_http_exchange_failed") from None


def _read_response(response: httpx.Response) -> bytes:
    body = bytearray()
    for chunk in response.iter_bytes():
        body.extend(chunk)
    return bytes(body)


def _retry_after(value: str | None, received_at: datetime) -> float | None:
    if value is None:
        return None
    try:
        if value.isascii() and value.isdecimal():
            seconds = float(value)
        else:
            deadline = parsedate_to_datetime(value)
            if deadline.tzinfo is None:
                return None
            seconds = max(0.0, (deadline - received_at).total_seconds())
        return seconds if math.isfinite(seconds) and seconds >= 0 else None
    except ValueError, TypeError, OverflowError:
        return None
