"""Bounded HTTPX wire client for the approved EDINET API exception."""

import logging
import math
from dataclasses import dataclass, field

import httpx
from pydantic import Field

from stock_research_llm_orchestrator.contracts.base import StrictContractModel
from stock_research_llm_orchestrator.requests.transport import UntrustedTransportResponse
from stock_research_llm_orchestrator.sources.edinet.transport import EdinetHttpTarget


_SUBSCRIPTION_KEY_PARAMETER = "Subscription-Key"
_DEPENDENCY_LOGGERS = ("httpx", "httpcore")


class EdinetHttpClientError(RuntimeError):
    """Sanitized failure from the EDINET HTTPX wire boundary."""


class EdinetHttpClientPolicy(StrictContractModel):
    """Explicit bounds for one EDINET HTTP exchange."""

    max_response_bytes: int = Field(ge=1)
    connect_timeout_seconds: float = Field(gt=0, allow_inf_nan=False)
    read_timeout_seconds: float = Field(gt=0, allow_inf_nan=False)
    write_timeout_seconds: float = Field(gt=0, allow_inf_nan=False)
    pool_timeout_seconds: float = Field(gt=0, allow_inf_nan=False)


@dataclass(frozen=True)
class HttpxEdinetWireClient:
    """Send one bounded request without exposing dependency request logs."""

    policy: EdinetHttpClientPolicy
    transport: httpx.BaseTransport | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        """Disable HTTP dependency logs before they can render a query credential."""
        for name in _DEPENDENCY_LOGGERS:
            logging.getLogger(name).disabled = True

    def __call__(self, target: EdinetHttpTarget, subscription_key: str) -> UntrustedTransportResponse:
        """Inject the approved query credential only for this physical send."""
        timeout = httpx.Timeout(
            connect=self.policy.connect_timeout_seconds,
            read=self.policy.read_timeout_seconds,
            write=self.policy.write_timeout_seconds,
            pool=self.policy.pool_timeout_seconds,
        )
        parameters = {item.name: item.value for item in target.query}
        parameters[_SUBSCRIPTION_KEY_PARAMETER] = subscription_key
        url = f"{target.scheme}://{target.origin}{target.path}"
        try:
            with (
                httpx.Client(
                    transport=self.transport,
                    timeout=timeout,
                    follow_redirects=False,
                    trust_env=False,
                ) as client,
                client.stream(target.method, url, params=parameters) as response,
            ):
                if 300 <= response.status_code < 400:
                    raise EdinetHttpClientError("edinet_http_redirect_rejected")
                body = _read_bounded(response, self.policy.max_response_bytes)
                media_type = response.headers.get("Content-Type", "").partition(";")[0].strip().lower()
                encoding = "utf-8" if target.path.endswith(".json") else "binary"
                return UntrustedTransportResponse(
                    status_code=response.status_code,
                    body=body,
                    media_type=media_type,
                    encoding=encoding,
                    final_origin=response.url.host,
                    redirected=False,
                    retry_after_seconds=_retry_after_seconds(response),
                )
        except EdinetHttpClientError:
            raise
        except Exception:
            raise EdinetHttpClientError("edinet_http_exchange_failed") from None


def _read_bounded(response: httpx.Response, limit: int) -> bytes:
    """Read automatically decompressed chunks while enforcing the decoded limit."""
    body = bytearray()
    for chunk in response.iter_bytes():
        if len(body) + len(chunk) > limit:
            raise EdinetHttpClientError("edinet_http_response_too_large")
        body.extend(chunk)
    return bytes(body)


def _retry_after_seconds(response: httpx.Response) -> float | None:
    """Return only a finite non-negative delta-seconds Retry-After value."""
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        return None
    return seconds if math.isfinite(seconds) and seconds >= 0 else None
