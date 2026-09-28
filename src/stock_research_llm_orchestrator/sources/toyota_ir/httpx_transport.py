"""One anonymous, redirect-free exchange for an exact pinned Toyota PDF."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlsplit

import httpx

from stock_research_llm_orchestrator.requests.transport import PhysicalTransportRequest, UntrustedTransportResponse
from stock_research_llm_orchestrator.sources.dukascopy.httpx_transport import _retry_after
from stock_research_llm_orchestrator.sources.protocol import CredentialFreeSourceIntent
from stock_research_llm_orchestrator.sources.toyota_ir import disclosure_policy
from stock_research_llm_orchestrator.sources.toyota_ir.production_policy import intent_for, load_policy


@dataclass(frozen=True)
class ToyotaIrTransport:
    """Validate target identity before accessing the network; never retry."""

    intent: CredentialFreeSourceIntent
    policy_bytes: bytes = field(repr=False)
    clock: Callable[[], datetime] = field(repr=False)
    on_received: Callable[[datetime], None] = field(repr=False)
    transport: httpx.BaseTransport | None = field(default=None, repr=False)

    latest_disclosures: bool = False

    def __call__(self, request: PhysicalTransportRequest) -> UntrustedTransportResponse:
        """Return exact decoded response bytes through the controlled boundary."""
        if self.latest_disclosures:
            candidate_policy = disclosure_policy.load_policy(self.policy_bytes)
            expected = disclosure_policy.intent_for(candidate_policy, self.intent.resource_key)
        else:
            policy = load_policy(self.policy_bytes)
            expected = intent_for(policy, self.intent.resource_key)
        if self.intent != expected:
            raise ValueError("toyota_ir_intent_invalid")
        if request != self.intent.to_transport_request(request.logical_request_id, request.physical_attempt_id):
            raise ValueError("toyota_ir_request_mismatch")
        url = dict((p.name, p.value) for p in self.intent.parameters)["url"]
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.netloc != "global.toyota" or parsed.query or parsed.fragment:
            raise ValueError("toyota_ir_target_invalid")
        try:
            with httpx.Client(
                transport=self.transport,
                follow_redirects=False,
                trust_env=False,
                timeout=httpx.Timeout(connect=10, read=30, write=10, pool=10),
            ) as client:
                response = client.get(url)
                received_at = self.clock()
                self.on_received(received_at)
                return UntrustedTransportResponse(
                    status_code=response.status_code,
                    body=response.content,
                    media_type=response.headers.get("Content-Type", "").partition(";")[0].strip().lower(),
                    encoding="binary",
                    final_origin=response.url.host,
                    redirected=300 <= response.status_code < 400,
                    retry_after_seconds=_retry_after(response.headers.get("Retry-After"), received_at),
                )
        except httpx.HTTPError:
            raise RuntimeError("toyota_ir_transport_failed") from None
