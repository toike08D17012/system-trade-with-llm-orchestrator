"""Permit-bound production bridge for one pinned Yahoo physical exchange."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from time import sleep as real_sleep
from typing import Any, Protocol, cast
from urllib.parse import urlsplit

from curl_cffi import requests

from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.research import AgentRoleValue
from stock_research_llm_orchestrator.requests.production import (
    CommittedRawReference,
    LogicalResultOutcome,
    RawPublicationIntent,
    RuntimeLease,
    RuntimeLeasePolicy,
)
from stock_research_llm_orchestrator.requests.raw_artifacts import RawArtifactPublisher
from stock_research_llm_orchestrator.requests.storage import ProductionRequestRepository, RuntimeStorageError
from stock_research_llm_orchestrator.requests.transport import (
    PhysicalTransportRequest,
    ProductionTransportCoordinator,
    ReceivedResponseValidationError,
    UntrustedTransportResponse,
)
from stock_research_llm_orchestrator.sources.yfinance.coordinated_session import (
    CoordinatedSession,
    EphemeralYahooRequest,
    YahooExchangeReceipt,
    YahooRequestIdentity,
    _classify,
)
from stock_research_llm_orchestrator.sources.yfinance.models import YfinanceDailyIntent
from stock_research_llm_orchestrator.sources.yfinance.private_runtime import private_runtime_active
from stock_research_llm_orchestrator.sources.yfinance.production_policy import YahooProductionPolicy


class YahooProductionPortError(RuntimeError):
    """Sanitized production boundary failure."""


def _require_live_acceptance() -> None:
    """No backend, including an injected callable, is authorized for live use yet."""
    raise YahooProductionPortError("yahoo_live_acceptance_required")


def _retry_after_seconds(value: str | None, now: datetime) -> float | None:
    if value is None:
        return None
    value = value.strip()
    if value.isascii() and value.isdigit():
        return float(value)
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            raise ValueError
        return max(0.0, (parsed - now).total_seconds())
    except ValueError, TypeError, OverflowError:
        raise ReceivedResponseValidationError("yahoo_retry_after_invalid") from None


def _normalize_response(
    response: Any, request: EphemeralYahooRequest, identity: YahooRequestIdentity, now: datetime
) -> UntrustedTransportResponse:
    """Validate a received response separately from a possibly-failed wire send."""
    try:
        body = response.content
        if not isinstance(body, bytes):
            raise ValueError
        final_url = response.url
        if not isinstance(final_url, str):
            raise ValueError
        requested_path = urlsplit(request.url).path or "/"
        symbol = requested_path.rsplit("/", 1)[-1]
        if (
            _classify(request.method, final_url, symbol) != identity
            or (urlsplit(final_url).path or "/") != requested_path
        ):
            raise ReceivedResponseValidationError("yahoo_response_identity_invalid")
        headers = {str(key).lower(): str(value) for key, value in response.headers.items()}
        content_type = headers.get("content-type", "").lower().split(";")
        media_type = content_type[0].strip()
        allowed = (
            ("application/json",)
            if identity.resource_class == "chart"
            else (("text/plain",) if identity.resource_class == "crumb" else ("text/html", "text/plain"))
        )
        if media_type not in allowed or len(content_type) > 2:
            raise ReceivedResponseValidationError("yahoo_content_type_invalid")
        if len(content_type) == 2:
            key, separator, value = content_type[1].strip().partition("=")
            if key.strip() != "charset" or not separator or value.strip().strip('"') != "utf-8":
                raise ReceivedResponseValidationError("yahoo_charset_invalid")
        body.decode("utf-8", errors="strict")
        return UntrustedTransportResponse(
            status_code=response.status_code,
            body=body,
            media_type=media_type,
            encoding="utf-8",
            final_origin=identity.origin,
            redirected=False,
            retry_after_seconds=_retry_after_seconds(headers.get("retry-after"), now),
        )
    except ReceivedResponseValidationError:
        raise
    except UnicodeDecodeError:
        raise ReceivedResponseValidationError("yahoo_utf8_invalid") from None
    except Exception:
        raise ReceivedResponseValidationError("yahoo_received_response_invalid") from None


class YahooWireBackend(Protocol):
    """One ephemeral backend send; no durable request arguments are exposed."""

    def __call__(self, request: EphemeralYahooRequest, max_response_bytes: None) -> object:
        """Return one opaque provider response without a body-size ceiling."""
        ...


class CurlCffiYahooBackend:
    """Send through the yfinance-visible cookie jar under the coordinator permit."""

    def __init__(self) -> None:
        """Require explicit binding to the injected yfinance session."""
        self._session: requests.Session | None = None

    def bind_session(self, session: requests.Session) -> None:
        """Bind once to the exact session whose cookies yfinance reads."""
        if self._session is not None or not isinstance(session, requests.Session):
            raise YahooProductionPortError("yahoo_session_bind_rejected")
        if session.retry.count != 0:
            raise YahooProductionPortError("yahoo_retry_enabled")
        self._session = session

    def __call__(self, request: EphemeralYahooRequest, max_response_bytes: None) -> object:
        """Stream one request without an application byte limit."""
        session = self._session
        if session is None:
            raise YahooProductionPortError("yahoo_session_not_bound")
        if session.retry.count != 0:
            raise YahooProductionPortError("yahoo_retry_enabled")
        content = bytearray()

        def collect(chunk: bytes) -> None:
            content.extend(chunk)

        kwargs = dict(request.kwargs)
        kwargs["allow_redirects"] = False
        kwargs["content_callback"] = collect
        # Call the base implementation explicitly. Calling session.request here
        # would recurse into CoordinatedSession.request and re-enter the port.
        wire_request = cast("Callable[..., Any]", requests.Session.request)
        response = wire_request(session, request.method, request.url, **kwargs)
        response.content = bytes(content)
        return response


class ProductionYahooExchangePort:
    """Bind one admitted logical leader to its permitted physical Yahoo sends."""

    def __init__(
        self,
        *,
        repository: ProductionRequestRepository,
        coordinator: ProductionTransportCoordinator,
        publisher: RawArtifactPublisher,
        logical_request_id: str,
        task_id: str,
        lease: RuntimeLease,
        policy: YahooProductionPolicy,
        role: AgentRoleValue,
        intent: YfinanceDailyIntent,
        attempt_id_factory: Callable[[], str],
        reservation_id_factory: Callable[[], str],
        publication_id_factory: Callable[[], str],
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        sleep: Callable[[float], None] = real_sleep,
        gate_wait_timeout_seconds: float = 15.0,
        gate_poll_seconds: float = 0.25,
        lease_policy: RuntimeLeasePolicy | None = None,
        backend: YahooWireBackend | None = None,
        live_verification: bool = False,
    ) -> None:
        """Require the already admitted logical leader and its runtime authority."""
        if type(policy) is not YahooProductionPolicy:
            raise YahooProductionPortError("yahoo_production_policy_required")
        policy.validate(clock().date())
        policy.gate_keys(
            YahooRequestIdentity("GET", "query2.finance.yahoo.com", "chart", "chart"), task_id=task_id, role=role
        )
        if live_verification:
            if not private_runtime_active() or (backend is not None and type(backend) is not CurlCffiYahooBackend):
                raise YahooProductionPortError("yahoo_private_verification_required")
            backend = CurlCffiYahooBackend() if backend is None else backend
        elif backend is None or isinstance(backend, CurlCffiYahooBackend):
            raise YahooProductionPortError("yahoo_live_acceptance_required")
        factories = (attempt_id_factory, reservation_id_factory, publication_id_factory)
        if not all(callable(factory) for factory in factories):
            raise YahooProductionPortError("yahoo_id_factory_required")
        if gate_wait_timeout_seconds <= 0 or gate_poll_seconds <= 0:
            raise YahooProductionPortError("yahoo_gate_wait_policy_invalid")
        self._repository = repository
        self._coordinator = coordinator
        self._publisher = publisher
        self._logical_request_id = logical_request_id
        self._task_id = task_id
        self._lease = lease
        self._policy = policy
        self._role = role
        self._intent = YfinanceDailyIntent.model_validate(intent.model_dump(warnings=False))
        self._fingerprint = policy.intent_fingerprint(self._intent, task_id=task_id, role=role)
        self._session: CoordinatedSession | None = None
        self._attempt_id_factory = attempt_id_factory
        self._reservation_id_factory = reservation_id_factory
        self._publication_id_factory = publication_id_factory
        self._clock = clock
        self._sleep = sleep
        self._gate_wait_timeout_seconds = gate_wait_timeout_seconds
        self._gate_poll_seconds = gate_poll_seconds
        self._lease_policy = lease_policy if lease_policy is not None else RuntimeLeasePolicy()
        self._backend = backend
        self._live_verification = live_verification
        self._references: list[CommittedRawReference] = []
        self._audit_references: list[CommittedRawReference] = []
        self._observations: list[dict[str, object]] = []
        self._poisoned = False
        self._uncertain = False
        self._finalized = False

    def bind_session(self, session: requests.Session) -> None:
        """Share yfinance's cookie jar with the permit-bound default backend."""
        if self._poisoned or self._finalized or self._session is not None or type(session) is not CoordinatedSession:
            raise YahooProductionPortError("yahoo_session_bind_rejected")
        session.assert_exchange_binding(self, self._intent)
        if isinstance(self._backend, CurlCffiYahooBackend):
            self._backend.bind_session(session)
        self._session = session

    @property
    def raw_references(self) -> tuple[CommittedRawReference, ...]:
        """Return immutable references published before provider responses escaped."""
        return tuple(self._references)

    @property
    def uncertain(self) -> bool:
        """Report a send with no validated response, requiring unknown finalization."""
        return self._uncertain

    @property
    def observations(self) -> tuple[dict[str, object], ...]:
        """Return only allowlisted response metadata, including validation failures."""
        return tuple(dict(item) for item in self._observations)

    def exchange(self, identity: YahooRequestIdentity, request: EphemeralYahooRequest) -> YahooExchangeReceipt:
        """Reserve, gate, send, validate, and publish before releasing response."""
        if self._live_verification:
            if not private_runtime_active():
                raise YahooProductionPortError("yahoo_private_verification_required")
        else:
            _require_live_acceptance()
        if self._poisoned or self._finalized:
            raise YahooProductionPortError("yahoo_port_poisoned")
        session = self._session
        if session is None:
            self._poisoned = True
            raise YahooProductionPortError("yahoo_session_not_bound")
        try:
            session.assert_exchange_binding(self, self._intent)
            session.assert_active_envelope(self, identity, request, consume=True)
        except Exception:
            self._poisoned = True
            raise YahooProductionPortError("yahoo_session_envelope_mismatch") from None
        if request.method != identity.method or urlsplit(request.url).hostname != identity.origin:
            self._poisoned = True
            raise YahooProductionPortError("yahoo_request_identity_mismatch")
        if request.kwargs.get("allow_redirects") is not False:
            self._poisoned = True
            raise YahooProductionPortError("yahoo_redirect_rejected")
        wait_started_at = self._clock()
        self._policy.validate(wait_started_at.date())
        keys = self._policy.gate_keys(identity, task_id=self._task_id, role=self._role)
        transport_policy = self._policy.transport_policy_for(identity)
        if _classify(request.method, request.url, self._intent.symbol) != identity:
            raise YahooProductionPortError("yahoo_request_identity_mismatch")

        def assert_binding(now: datetime) -> None:
            try:
                session.assert_exchange_binding(self, self._intent)
                session.assert_active_envelope(self, identity, request, consume=False)
                self._policy.validate(now.date())
                self._repository.assert_claimed_logical_request(
                    self._logical_request_id,
                    task_id=self._task_id,
                    source_id="yfinance",
                    operation="daily-history",
                    source_approval_version=2,
                    source_profile_version=3,
                    credential_scope_alias=None,
                    egress_scope=keys.egress,
                    rate_domain=self._policy.rate_domain,
                    lease=self._lease,
                    now=now,
                    request_fingerprint=self._fingerprint,
                )
            except Exception:
                self._poisoned = True
                raise

        def send(physical: PhysicalTransportRequest, envelope: object) -> tuple[UntrustedTransportResponse, object]:
            if physical != sanitized or envelope is not request:
                raise YahooProductionPortError("yahoo_transport_boundary_mismatch")
            assert_binding(self._clock())
            response = cast("Any", self._backend(request, transport_policy.max_response_bytes))
            observed_at = self._clock()
            headers = {str(key).lower(): str(value) for key, value in response.headers.items()}
            media_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
            observation: dict[str, object] = {
                "operation": identity.endpoint,
                "retrieved_at": observed_at.isoformat(),
                "logical_request_id": self._logical_request_id,
                "physical_attempt_id": physical.physical_attempt_id,
                "status_code": response.status_code if type(response.status_code) is int else None,
                "media_type": media_type
                if media_type in {"application/json", "text/html", "text/plain"}
                else "unapproved",
                "byte_count": len(response.content) if isinstance(response.content, bytes) else None,
                "validation": "failed",
                "reason_code": "invalid_transport_response",
            }
            self._observations.append(observation)
            common = _normalize_response(response, request, identity, observed_at)
            observation.update(validation="passed", reason_code="ok", encoding="utf-8")
            return common, response

        while True:
            started_at = self._clock()
            self._policy.validate(started_at.date())
            assert_binding(started_at)
            attempt = self._repository.reserve_next_physical_attempt(
                self._attempt_id_factory(), self._logical_request_id, self._lease, started_at
            )
            sanitized = PhysicalTransportRequest(
                logical_request_id=self._logical_request_id,
                physical_attempt_id=attempt.physical_attempt_id,
                origin=identity.origin,
                operation=identity.endpoint,
                resource_key=identity.resource_class,
            )
            try:
                exchange = self._coordinator.execute_exchange_with_callback(
                    sanitized,
                    attempt,
                    self._reservation_id_factory(),
                    keys,
                    self._policy.gate_policy(),
                    transport_policy,
                    self._lease,
                    started_at,
                    self._clock,
                    request,
                    send,
                )
                break
            except Exception as error:
                try:
                    self._repository.discard_reserved_attempt(attempt.physical_attempt_id, self._lease, self._clock())
                except Exception:
                    self._uncertain = self._poisoned = True
                    raise YahooProductionPortError("yahoo_gate_or_exchange_failed") from None
                blocked = isinstance(error, RuntimeStorageError) and str(error).startswith("gate_blocked:")
                elapsed = (self._clock() - wait_started_at).total_seconds()
                if not blocked or elapsed >= self._gate_wait_timeout_seconds:
                    self._poisoned = True
                    raise YahooProductionPortError("yahoo_gate_wait_exhausted") from None
                self._lease = self._repository.heartbeat_lease(self._lease, self._clock(), self._lease_policy)
                self._sleep(min(self._gate_poll_seconds, self._gate_wait_timeout_seconds - elapsed))
        execution = exchange.execution
        if execution.status == "unknown":
            self._uncertain = self._poisoned = True
            raise YahooProductionPortError("yahoo_exchange_outcome_unknown")
        if execution.candidate is None or exchange.status_code is None or exchange.provider_response is None:
            self._poisoned = True
            raise YahooProductionPortError("yahoo_exchange_unvalidated")
        candidate = execution.candidate
        self._observations[-1]["reason_code"] = (
            "cookie_404_continue"
            if identity.endpoint == "cookie_basic" and exchange.status_code == 404
            else execution.reason_code
        )
        auxiliary = identity.resource_class != "chart"
        if auxiliary:
            body = json.dumps({**self._observations[-1], "body_retained": False}, sort_keys=True).encode()
            candidate = replace(
                candidate,
                body=body,
                sha256=hashlib.sha256(body).hexdigest(),
                media_type="application/json",
                encoding="utf-8",
            )
        intent = RawPublicationIntent(
            publication_id=self._publication_id_factory(),
            task_id=self._task_id,
            logical_request_id=self._logical_request_id,
            physical_attempt_id=attempt.physical_attempt_id,
            source_id="yfinance",
            operation="daily-history",
            content_sha256=candidate.sha256,
            byte_count=len(candidate.body),
            media_type=candidate.media_type,
            encoding=candidate.encoding,
            raw_schema_id="yfinance-auxiliary-audit" if auxiliary else "yfinance-http-response",
            raw_schema_version=1,
            publication_generation=self._lease.generation,
        )
        try:
            reference = self._publisher.publish(
                candidate, intent, self._lease, started_at, self._clock(), lambda body: None
            )
        except Exception:
            self._poisoned = True
            raise YahooProductionPortError("yahoo_raw_publication_failed") from None
        if auxiliary:
            self._audit_references.append(reference)
            return YahooExchangeReceipt(exchange.status_code, exchange.provider_response, None, reference)
        self._references.append(reference)
        return YahooExchangeReceipt(exchange.status_code, exchange.provider_response, reference)

    def finalize(self, outcome: LogicalResultOutcome, error_code: str | None = None) -> None:
        """Commit one logical result after the adapter has finished parsing."""
        if self._finalized:
            raise YahooProductionPortError("yahoo_logical_already_finalized")
        if self._uncertain:
            outcome, error_code = LogicalResultOutcome.UNKNOWN, "transport_outcome_unknown"
        elif self._poisoned and outcome is LogicalResultOutcome.SUCCEEDED:
            raise YahooProductionPortError("yahoo_poisoned_success_rejected")
        self._coordinator.finalize_logical_request(
            self._logical_request_id, outcome, self._lease, self._clock(), error_code
        )
        self._finalized = True
