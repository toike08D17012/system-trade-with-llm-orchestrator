"""Synthetic production bridge tests; no Yahoo request is sent."""

import sqlite3
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from curl_cffi import requests

from stock_research_llm_orchestrator.configuration import validate_configuration
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.research import AgentRole
from stock_research_llm_orchestrator.requests.models import SourceBinding, bind_source
from stock_research_llm_orchestrator.requests.production import (
    CommittedRawReference,
    LogicalResultOutcome,
    QueuePolicy,
    RuntimeLeasePolicy,
)
from stock_research_llm_orchestrator.requests.raw_artifacts import RawArtifactPublisher
from stock_research_llm_orchestrator.requests.storage import (
    DATABASE_FILENAME,
    ProductionRequestRepository,
    RuntimeStorageError,
    initialize_runtime_storage,
)
from stock_research_llm_orchestrator.requests.transport import (
    PhysicalTransportRequest,
    ProductionTransportCoordinator,
    UntrustedTransportResponse,
)
from stock_research_llm_orchestrator.sources.yfinance.coordinated_session import (
    CoordinatedSession,
    CoordinatedSessionError,
    EphemeralYahooRequest,
    YahooExchangeReceipt,
    YahooRequestIdentity,
)
from stock_research_llm_orchestrator.sources.yfinance.models import YfinanceDailyIntent
from stock_research_llm_orchestrator.sources.yfinance.private_runtime import private_yfinance_runtime
from stock_research_llm_orchestrator.sources.yfinance.production_policy import (
    compose_yahoo_production_policy,
)
from stock_research_llm_orchestrator.sources.yfinance.production_port import (
    CurlCffiYahooBackend,
    ProductionYahooExchangePort,
    YahooProductionPortError,
    YahooWireBackend,
    _require_live_acceptance,
)


NOW = datetime(2026, 9, 25, tzinfo=UTC)
IDENTITY = YahooRequestIdentity("GET", "query2.finance.yahoo.com", "chart", "chart")
INTENT = YfinanceDailyIntent(
    symbol="7203.T",
    jpx_code="7203",
    jpx_snapshot_on="2026-08-31",
    mic="XTKS",
    market_segment="Prime",
    start="2026-09-01",
    end="2026-09-03",
)
HISTORY_PARAMS = {
    "period1": 1788188400,
    "period2": 1788361200,
    "interval": "1d",
    "includePrePost": False,
    "events": "div,splits,capitalGains",
}
REQUEST = EphemeralYahooRequest(
    "GET", "https://query2.finance.yahoo.com/v8/finance/chart/7203.T", {"allow_redirects": False}
)


@pytest.fixture(autouse=True)
def _offline_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Lift the unconditional live block only in this test module and forbid actual curl IO."""
    monkeypatch.setattr(
        "stock_research_llm_orchestrator.sources.yfinance.production_port._require_live_acceptance", lambda: None
    )

    def deny_wire(*args: object, **kwargs: object) -> None:
        raise AssertionError("unexpected_live_wire")

    monkeypatch.setattr(requests.Session, "request", deny_wire)


def _prepared(
    tmp_path: Path,
    backend: YahooWireBackend | None,
    *,
    clock: Callable[[], datetime] | None = None,
    sleep: Callable[[float], None] | None = None,
    bind_session: bool = True,
    live_verification: bool = False,
) -> tuple[ProductionYahooExchangePort, ProductionRequestRepository, Path]:
    runtime = tmp_path / ".runtime"
    runtime.mkdir(mode=0o700)
    runs = tmp_path / "runs"
    runs.mkdir(mode=0o700)
    repository = initialize_runtime_storage(runtime)
    lease = repository.acquire_lease("owner-yf", NOW, RuntimeLeasePolicy())
    policy = compose_yahoo_production_policy(_reviewed_binding())
    logical = policy.logical_request(
        logical_request_id="logical-yf",
        task_id="task-yf",
        request_fingerprint=policy.intent_fingerprint(INTENT, task_id="task-yf", role=AgentRole.CODEX_WORKER),
        created_at=NOW,
    )
    repository.admit_logical_request(logical, policy.rate_domain, policy.cache_policy(), lease, NOW, QueuePolicy())
    assert repository.claim_next_queued(policy.rate_domain, lease, NOW + timedelta(seconds=1))
    current = [NOW + timedelta(seconds=2)]
    ids = iter(range(1, 100))

    def default_clock() -> datetime:
        return current[0]

    def default_sleep(seconds: float) -> None:
        current[0] += timedelta(seconds=seconds)

    def forbidden_legacy_send(request: PhysicalTransportRequest) -> UntrustedTransportResponse:
        raise AssertionError("legacy_transport_must_not_be_called")

    port = ProductionYahooExchangePort(
        repository=repository,
        coordinator=ProductionTransportCoordinator(repository, forbidden_legacy_send, lambda: f"permit-{next(ids)}"),
        publisher=RawArtifactPublisher(runs, repository),
        logical_request_id="logical-yf",
        task_id="task-yf",
        lease=lease,
        policy=policy,
        role=AgentRole.CODEX_WORKER,
        intent=INTENT,
        attempt_id_factory=lambda: f"attempt-{next(ids)}",
        reservation_id_factory=lambda: f"reservation-{next(ids)}",
        publication_id_factory=lambda: f"publication-{next(ids)}",
        clock=clock if clock is not None else default_clock,
        sleep=sleep if sleep is not None else default_sleep,
        backend=backend,
        live_verification=live_verification,
    )
    if bind_session:
        CoordinatedSession(exchange=port, expected_symbol=INTENT.symbol, intent=INTENT)
    return port, repository, runs


def _dispatch(
    port: ProductionYahooExchangePort, identity: YahooRequestIdentity, request: EphemeralYahooRequest
) -> YahooExchangeReceipt:
    session = port._session
    assert session is not None
    original = port.exchange
    receipts: list[YahooExchangeReceipt] = []
    errors: list[Exception] = []

    def capture(actual: YahooRequestIdentity, envelope: EphemeralYahooRequest) -> YahooExchangeReceipt:
        try:
            receipt = original(identity, envelope)
            receipts.append(receipt)
            return receipt
        except Exception as error:
            errors.append(error)
            raise

    kwargs = dict(request.kwargs)
    if request is REQUEST:
        kwargs["params"] = {"range": "1d", "interval": "1d"} if not port.raw_references else dict(HISTORY_PARAMS)
    patch = pytest.MonkeyPatch()
    patch.setattr(port, "exchange", capture)
    try:
        try:
            session.request(request.method, request.url, **kwargs)
        except CoordinatedSessionError:
            if errors:
                raise errors[0] from None
            if receipts and receipts[-1].status_code == 429:
                return receipts[-1]
            raise
        return receipts[0]
    finally:
        patch.undo()


def _reviewed_binding() -> SourceBinding:
    binding = bind_source(validate_configuration(Path("config")), "yfinance", 3, 3, date(2026, 9, 26))
    assert isinstance(binding, SourceBinding)
    return binding


def _response(
    status: int = 200,
    body: bytes = b'{"chart":{}}',
    content_type: str = "application/json",
    extra_headers: dict[str, str] | None = None,
) -> SimpleNamespace:
    headers = {"Content-Type": content_type}
    headers.update(extra_headers or {})
    return SimpleNamespace(
        status_code=status,
        content=body,
        text=body.decode(errors="replace"),
        url=REQUEST.url,
        headers=headers,
        cookies={},
        json=lambda: {},
    )


def test_each_exchange_publishes_exact_bytes_and_finalizes_once(tmp_path: Path) -> None:
    """Two sends each receive their own published raw reference."""
    port, repository, runs = _prepared(tmp_path, lambda request, limit: _response())
    first = _dispatch(port, IDENTITY, REQUEST)
    second = _dispatch(port, IDENTITY, REQUEST)
    assert first.status_code == 200
    assert len(port.raw_references) == 2
    assert (
        cast("CommittedRawReference", first.raw_reference).physical_attempt_id
        != cast("CommittedRawReference", second.raw_reference).physical_attempt_id
    )
    for reference in port.raw_references:
        assert (runs / reference.task_id / reference.relative_path / "body.bin").read_bytes() == b'{"chart":{}}'
    port.finalize(LogicalResultOutcome.SUCCEEDED)
    assert repository.logical_request_state("logical-yf") == "succeeded"
    with pytest.raises(YahooProductionPortError, match="already_finalized"):
        port.finalize(LogicalResultOutcome.SUCCEEDED)


def test_known_status_publishes_its_response(tmp_path: Path) -> None:
    """A known provider error still preserves its exact response."""
    port, repository, _ = _prepared(tmp_path, lambda request, limit: _response(404, b"not found"))
    receipt = _dispatch(port, IDENTITY, REQUEST)
    assert receipt.status_code == 404
    assert cast("CommittedRawReference", receipt.raw_reference).byte_count == 9
    port.finalize(LogicalResultOutcome.FAILED, "provider_error")
    assert repository.logical_request_state("logical-yf") == "failed"


def test_unknown_send_poisons_and_finalizes_unknown(tmp_path: Path) -> None:
    """An uncertain send stops the session and finalizes as unknown."""

    def broken(request: EphemeralYahooRequest, limit: None) -> object:
        raise RuntimeError("secret URL")

    port, repository, _ = _prepared(tmp_path, cast("YahooWireBackend", broken))
    with pytest.raises(YahooProductionPortError, match="outcome_unknown"):
        _dispatch(port, IDENTITY, REQUEST)
    assert port.uncertain
    port.finalize(LogicalResultOutcome.FAILED, "source_error")
    assert repository.logical_request_state("logical-yf") == "failed"


def test_response_above_previous_limit_is_published(tmp_path: Path) -> None:
    """The approved unbounded Yahoo policy preserves responses above the former cap."""
    body = b'{"x":"' + b"x" * (32 * 1024 * 1024) + b'"}'

    def backend(request: EphemeralYahooRequest, limit: None) -> object:
        assert limit is None
        return _response(body=body)

    port, _, runs = _prepared(tmp_path, cast("YahooWireBackend", backend))
    receipt = _dispatch(port, IDENTITY, REQUEST)
    assert not port.uncertain
    reference = cast("CommittedRawReference", receipt.raw_reference)
    assert reference.byte_count == len(body)
    assert (runs / reference.task_id / reference.relative_path / "body.bin").read_bytes() == body


@pytest.mark.parametrize(
    ("body", "content_type"),
    [
        (b"\xff", "application/json"),
        (b'{"chart":{}}', "text/html"),
        (b'{"chart":{}}', "application/json; charset=shift_jis"),
        (b'{"chart":{}}', "application/json; charset=utf-8; extra=value"),
    ],
)
def test_invalid_utf8_or_content_type_is_never_published(tmp_path: Path, body: bytes, content_type: str) -> None:
    """Reject chart bytes outside the reviewed JSON and UTF-8 response contract."""
    port, _, _ = _prepared(tmp_path, lambda request, limit: _response(body=body, content_type=content_type))
    with pytest.raises(YahooProductionPortError, match="exchange_unvalidated"):
        _dispatch(port, IDENTITY, REQUEST)
    assert not port.uncertain
    assert port.raw_references == ()


def test_exact_32_mib_json_with_case_insensitive_utf8_charset_is_accepted(tmp_path: Path) -> None:
    """Accept the reviewed byte boundary and normalized JSON content-type spelling."""
    size = 32 * 1024 * 1024
    body = b'{"x":"' + (b"a" * (size - 8)) + b'"}'
    assert len(body) == size
    port, _, runs = _prepared(
        tmp_path,
        lambda request, limit: _response(body=body, content_type='Application/JSON; Charset="UTF-8"'),
    )

    receipt = _dispatch(port, IDENTITY, REQUEST)

    reference = cast("CommittedRawReference", receipt.raw_reference)
    assert reference.byte_count == size
    assert (runs / reference.task_id / reference.relative_path / "body.bin").stat().st_size == size


def test_gate_waits_between_physical_sends_without_resending(tmp_path: Path) -> None:
    """The approved 2.5-second gap delays the next send while preserving one callback per request."""
    current = [NOW + timedelta(seconds=2)]
    sends: list[datetime] = []

    def backend(request: EphemeralYahooRequest, limit: None) -> object:
        sends.append(current[0])
        return _response()

    def sleep(seconds: float) -> None:
        current[0] += timedelta(seconds=seconds)

    port, _, _ = _prepared(tmp_path, cast("YahooWireBackend", backend), clock=lambda: current[0], sleep=sleep)
    _dispatch(port, IDENTITY, REQUEST)
    _dispatch(port, IDENTITY, REQUEST)
    assert len(sends) == 2
    assert (sends[1] - sends[0]).total_seconds() >= 2.5
    assert len(port.raw_references) == 2


def test_distinct_logical_requests_share_yahoo_finance_provider_gate(tmp_path: Path) -> None:
    """Apply one yahoo-finance rate domain across independently admitted logical requests."""
    runtime = tmp_path / ".runtime"
    runtime.mkdir(mode=0o700)
    runs = tmp_path / "runs"
    runs.mkdir(mode=0o700)
    repository = initialize_runtime_storage(runtime)
    lease = repository.acquire_lease("owner-yf", NOW, RuntimeLeasePolicy())
    policy = compose_yahoo_production_policy(_reviewed_binding())
    for index in (1, 2):
        logical = policy.logical_request(
            logical_request_id=f"logical-yf-{index}",
            task_id=f"task-yf-{index}",
            request_fingerprint=policy.intent_fingerprint(
                INTENT, task_id=f"task-yf-{index}", role=AgentRole.CODEX_WORKER
            ),
            created_at=NOW,
        )
        repository.admit_logical_request(logical, policy.rate_domain, policy.cache_policy(), lease, NOW, QueuePolicy())
    assert repository.claim_next_queued(policy.rate_domain, lease, NOW + timedelta(seconds=1))
    assert repository.claim_next_queued(policy.rate_domain, lease, NOW + timedelta(seconds=1))
    current = [NOW + timedelta(seconds=2)]
    sends: list[datetime] = []
    ids = iter(range(1, 100))

    def backend(request: EphemeralYahooRequest, limit: None) -> object:
        sends.append(current[0])
        return _response()

    def sleep(seconds: float) -> None:
        current[0] += timedelta(seconds=seconds)

    def forbidden_legacy_send(request: PhysicalTransportRequest) -> UntrustedTransportResponse:
        raise AssertionError("legacy_transport_must_not_be_called")

    def make_port(index: int) -> ProductionYahooExchangePort:
        port = ProductionYahooExchangePort(
            repository=repository,
            coordinator=ProductionTransportCoordinator(
                repository, forbidden_legacy_send, lambda: f"permit-{next(ids)}"
            ),
            publisher=RawArtifactPublisher(runs, repository),
            logical_request_id=f"logical-yf-{index}",
            task_id=f"task-yf-{index}",
            lease=lease,
            policy=policy,
            role=AgentRole.CODEX_WORKER,
            intent=INTENT,
            attempt_id_factory=lambda: f"attempt-{next(ids)}",
            reservation_id_factory=lambda: f"reservation-{next(ids)}",
            publication_id_factory=lambda: f"publication-{next(ids)}",
            clock=lambda: current[0],
            sleep=sleep,
            backend=cast("YahooWireBackend", backend),
        )

        CoordinatedSession(exchange=port, expected_symbol=INTENT.symbol, intent=INTENT)
        return port

    first, second = make_port(1), make_port(2)
    _dispatch(first, IDENTITY, REQUEST)
    _dispatch(second, IDENTITY, REQUEST)

    assert len(sends) == 2
    assert (sends[1] - sends[0]).total_seconds() == 2.5
    assert policy.rate_domain == "yahoo-finance"


@pytest.mark.parametrize(
    "retry_after",
    ["7", "Fri, 25 Sep 2026 00:00:09 GMT"],
)
def test_retry_after_honors_shared_provider_cooldown_across_logical_requests(tmp_path: Path, retry_after: str) -> None:
    """Honor integer and HTTP-date cooldowns from case-insensitive headers across leaders."""
    runtime = tmp_path / ".runtime"
    runtime.mkdir(mode=0o700)
    runs = tmp_path / "runs"
    runs.mkdir(mode=0o700)
    repository = initialize_runtime_storage(runtime)
    lease = repository.acquire_lease("owner-yf", NOW, RuntimeLeasePolicy())
    policy = compose_yahoo_production_policy(_reviewed_binding())
    for index in (1, 2):
        logical = policy.logical_request(
            logical_request_id=f"logical-yf-{index}",
            task_id=f"task-yf-{index}",
            request_fingerprint=policy.intent_fingerprint(
                INTENT, task_id=f"task-yf-{index}", role=AgentRole.CODEX_WORKER
            ),
            created_at=NOW,
        )
        repository.admit_logical_request(logical, policy.rate_domain, policy.cache_policy(), lease, NOW, QueuePolicy())
    assert repository.claim_next_queued(policy.rate_domain, lease, NOW + timedelta(seconds=1))
    assert repository.claim_next_queued(policy.rate_domain, lease, NOW + timedelta(seconds=1))
    current = [NOW + timedelta(seconds=2)]
    sends: list[datetime] = []
    responses = iter(
        (
            _response(429, extra_headers={"rEtRy-AfTeR": retry_after}),
            _response(),
        )
    )
    ids = iter(range(1, 100))

    def backend(request: EphemeralYahooRequest, limit: None) -> object:
        sends.append(current[0])
        return next(responses)

    def sleep(seconds: float) -> None:
        current[0] += timedelta(seconds=seconds)

    def forbidden_legacy_send(request: PhysicalTransportRequest) -> UntrustedTransportResponse:
        raise AssertionError("legacy_transport_must_not_be_called")

    def make_port(index: int) -> ProductionYahooExchangePort:
        port = ProductionYahooExchangePort(
            repository=repository,
            coordinator=ProductionTransportCoordinator(
                repository, forbidden_legacy_send, lambda: f"permit-{next(ids)}"
            ),
            publisher=RawArtifactPublisher(runs, repository),
            logical_request_id=f"logical-yf-{index}",
            task_id=f"task-yf-{index}",
            lease=lease,
            policy=policy,
            role=AgentRole.CODEX_WORKER,
            intent=INTENT,
            attempt_id_factory=lambda: f"attempt-{next(ids)}",
            reservation_id_factory=lambda: f"reservation-{next(ids)}",
            publication_id_factory=lambda: f"publication-{next(ids)}",
            clock=lambda: current[0],
            sleep=sleep,
            backend=cast("YahooWireBackend", backend),
        )

        CoordinatedSession(exchange=port, expected_symbol=INTENT.symbol, intent=INTENT)
        return port

    first, second = make_port(1), make_port(2)
    assert _dispatch(first, IDENTITY, REQUEST).status_code == 429
    assert _dispatch(second, IDENTITY, REQUEST).status_code == 200

    assert len(sends) == 2
    assert (sends[1] - sends[0]).total_seconds() == 7


def test_malformed_retry_after_is_known_failure_without_raw(tmp_path: Path) -> None:
    """Reject a received malformed cooldown without converting it to an unknown send."""
    port, repository, _ = _prepared(
        tmp_path,
        lambda request, limit: _response(429, extra_headers={"Retry-After": "not-a-date"}),
    )

    with pytest.raises(YahooProductionPortError, match="exchange_unvalidated"):
        _dispatch(port, IDENTITY, REQUEST)

    assert not port.uncertain
    assert port.raw_references == ()
    assert repository.physical_attempt_state("attempt-1") == ("failed", 1)


def test_absent_retry_after_does_not_fabricate_cooldown(tmp_path: Path) -> None:
    """A received 429 without Retry-After does not invent a durable cooldown."""
    port, repository, _ = _prepared(tmp_path, lambda request, limit: _response(429))
    assert _dispatch(port, IDENTITY, REQUEST).status_code == 429
    assert port._session is not None and port._session.poisoned
    with sqlite3.connect(tmp_path / ".runtime" / DATABASE_FILENAME) as connection:
        assert connection.execute("SELECT COUNT(*) FROM gate_state WHERE cooldown_until IS NOT NULL").fetchone() == (0,)


@pytest.mark.parametrize(
    ("identity", "envelope"),
    [
        (
            YahooRequestIdentity("GET", "fc.yahoo.com", "cookie", "cookie_basic"),
            EphemeralYahooRequest("GET", "https://fc.yahoo.com", {"allow_redirects": False}),
        ),
        (
            YahooRequestIdentity("GET", "query1.finance.yahoo.com", "crumb", "crumb_basic"),
            EphemeralYahooRequest(
                "GET",
                "https://query1.finance.yahoo.com/v1/test/getcrumb",
                {"allow_redirects": False},
            ),
        ),
        (
            YahooRequestIdentity("GET", "query2.finance.yahoo.com", "crumb", "crumb_csrf"),
            EphemeralYahooRequest(
                "GET",
                "https://query2.finance.yahoo.com/v1/test/getcrumb",
                {"allow_redirects": False},
            ),
        ),
        (
            YahooRequestIdentity("GET", "guce.yahoo.com", "consent", "consent_form"),
            EphemeralYahooRequest("GET", "https://guce.yahoo.com/consent", {"allow_redirects": False}),
        ),
        (
            YahooRequestIdentity("POST", "consent.yahoo.com", "consent", "consent_collect"),
            EphemeralYahooRequest(
                "POST",
                "https://consent.yahoo.com/v2/collectConsent",
                {"allow_redirects": False},
            ),
        ),
        (
            YahooRequestIdentity("GET", "guce.yahoo.com", "consent", "consent_copy"),
            EphemeralYahooRequest("GET", "https://guce.yahoo.com/copyConsent", {"allow_redirects": False}),
        ),
    ],
)
def test_auxiliary_operations_persist_only_metadata(
    tmp_path: Path, identity: YahooRequestIdentity, envelope: EphemeralYahooRequest
) -> None:
    """Auxiliary tokens never enter durable raw artifacts, logs, or hashes."""
    import hashlib

    secret = b"temporary-secret-canary"

    def backend(candidate: EphemeralYahooRequest, limit: None) -> object:
        response = _response(body=secret, content_type="text/plain")
        response.url = candidate.url
        return response

    port, repository, runs = _prepared(tmp_path, cast("YahooWireBackend", backend))
    receipt = _dispatch(port, identity, envelope)
    assert receipt.raw_reference is None
    assert isinstance(receipt.audit_reference, CommittedRawReference)
    assert receipt.audit_reference.raw_schema_id == "yfinance-auxiliary-audit"
    assert port.raw_references == ()
    assert repository.physical_attempt_state("attempt-1") == ("succeeded", 1)
    for path in tmp_path.rglob("*"):
        if path.is_file():
            body = path.read_bytes()
            assert secret not in body
            assert hashlib.sha256(secret).hexdigest().encode() not in body
    audit = runs / receipt.audit_reference.task_id / receipt.audit_reference.relative_path / "body.bin"
    assert b'"body_retained": false' in audit.read_bytes()


def test_persisted_lineage_mismatch_rejects_before_attempt_or_send(tmp_path: Path) -> None:
    """Recheck durable lineage instead of trusting the in-memory reviewed policy."""
    sends: list[EphemeralYahooRequest] = []

    def backend(candidate: EphemeralYahooRequest, limit: None) -> object:
        sends.append(candidate)
        return _response()

    port, repository, _ = _prepared(tmp_path, cast("YahooWireBackend", backend))
    with sqlite3.connect(tmp_path / ".runtime" / DATABASE_FILENAME) as connection:
        connection.execute(
            "UPDATE logical_requests SET source_profile_version = 2 WHERE logical_request_id = 'logical-yf'"
        )

    with pytest.raises(RuntimeStorageError, match="logical_request_lineage_mismatch"):
        _dispatch(port, IDENTITY, REQUEST)

    assert sends == []
    assert repository.physical_attempt_state("attempt-1") is None
    assert repository.gate_events("logical-yf") == ()


def test_unknown_identity_rejects_before_attempt_or_send(tmp_path: Path) -> None:
    """Reject a non-reviewed identity before creating durable physical work."""
    sends: list[EphemeralYahooRequest] = []

    def backend(candidate: EphemeralYahooRequest, limit: None) -> object:
        sends.append(candidate)
        return _response()

    port, repository, _ = _prepared(tmp_path, cast("YahooWireBackend", backend))
    unknown = YahooRequestIdentity("GET", "query2.finance.yahoo.com", "chart", "unknown_chart")

    with pytest.raises(YahooProductionPortError, match="session_envelope_mismatch"):
        _dispatch(port, unknown, REQUEST)

    assert sends == []
    assert repository.physical_attempt_state("attempt-1") is None


def test_spoofed_request_identity_rejects_before_attempt_or_send(tmp_path: Path) -> None:
    """Reject an envelope whose origin differs from its sanitized identity."""
    sends: list[EphemeralYahooRequest] = []

    def backend(candidate: EphemeralYahooRequest, limit: None) -> object:
        sends.append(candidate)
        return _response()

    port, repository, _ = _prepared(tmp_path, cast("YahooWireBackend", backend))
    spoofed = EphemeralYahooRequest(
        "GET",
        "https://query1.finance.yahoo.com/v8/finance/chart/7203.T",
        {"allow_redirects": False},
    )

    with pytest.raises(CoordinatedSessionError, match="unapproved_yahoo_endpoint"):
        _dispatch(port, IDENTITY, spoofed)

    assert sends == []
    assert repository.physical_attempt_state("attempt-1") is None


def test_curl_backend_uses_bound_session_cookie_jar_without_recursion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The base curl send sees yfinance's cookies and enters the backend once."""
    seen: list[requests.Session] = []

    def base_request(bound: requests.Session, method: str, url: str, **kwargs: Any) -> requests.Response:
        seen.append(bound)
        assert bound.cookies is session.cookies
        assert bound.cookies.get("B") == "C"
        assert kwargs["allow_redirects"] is False
        kwargs["content_callback"](b'{"chart":{}}')
        response = requests.Response()
        response.status_code = 200
        response.url = url
        response.headers["Content-Type"] = "application/json"
        return response

    monkeypatch.setattr(requests.Session, "request", base_request)
    backend = CurlCffiYahooBackend()
    session = requests.Session()
    session.cookies.set("B", "C")
    backend.bind_session(session)
    result = cast("requests.Response", backend(REQUEST, None))
    assert result.content == b'{"chart":{}}'
    assert seen == [session]


@pytest.mark.parametrize("backend", [None, CurlCffiYahooBackend()])
def test_live_backend_construction_requires_explicit_acceptance(
    tmp_path: Path, backend: YahooWireBackend | None
) -> None:
    """Neither the default nor real curl backend can enable live transport yet."""
    with pytest.raises(YahooProductionPortError, match="yahoo_live_acceptance_required"):
        _prepared(tmp_path, backend)


def test_wrapping_a_live_backend_cannot_bypass_acceptance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An injected callable never authorizes live sends; only tests can replace the private guard."""
    inner = CurlCffiYahooBackend()
    called: list[bool] = []

    def wrapper(request: EphemeralYahooRequest, max_bytes: None) -> object:
        called.append(True)
        return inner(request, max_bytes)

    port, _, _ = _prepared(tmp_path, cast("YahooWireBackend", wrapper))
    monkeypatch.setattr(
        "stock_research_llm_orchestrator.sources.yfinance.production_port._require_live_acceptance",
        _require_live_acceptance,
    )
    with pytest.raises(YahooProductionPortError, match="yahoo_live_acceptance_required"):
        _dispatch(port, IDENTITY, REQUEST)
    assert called == []
    assert port.raw_references == ()


def _assert_no_physical_work(port: ProductionYahooExchangePort, tmp_path: Path) -> None:
    assert port.raw_references == ()
    with sqlite3.connect(tmp_path / ".runtime" / DATABASE_FILENAME) as connection:
        for table in ("physical_attempts", "gate_events", "gate_reservations", "raw_publications"):
            assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone() == (0,)


@pytest.mark.parametrize("bound", [True, False])
def test_direct_envelope_is_never_authorized(tmp_path: Path, bound: bool) -> None:
    """Even a matching public envelope cannot impersonate active session dispatch."""
    sends: list[object] = []
    port, _, _ = _prepared(tmp_path, lambda request, limit: sends.append(request), bind_session=bound)
    with pytest.raises(YahooProductionPortError, match="session_"):
        port.exchange(IDENTITY, REQUEST)
    assert sends == []
    _assert_no_physical_work(port, tmp_path)


@pytest.mark.parametrize(
    "changes",
    [
        {"start": "2026-08-01"},
        {"end": "2026-09-04"},
        {"jpx_snapshot_on": "2026-08-30"},
        {"market_segment": "Standard"},
        {"symbol": "1301.T", "jpx_code": "1301"},
    ],
)
def test_full_session_intent_must_match_port(tmp_path: Path, changes: dict[str, str]) -> None:
    """Same-symbol requests with different dates or JPX evidence are different bindings."""
    port, _, _ = _prepared(tmp_path, lambda request, limit: _response(), bind_session=False)
    intent = YfinanceDailyIntent.model_validate({**INTENT.model_dump(), **changes})
    with pytest.raises(CoordinatedSessionError, match="intent_mismatch"):
        CoordinatedSession(exchange=port, expected_symbol=intent.symbol, intent=intent)
    _assert_no_physical_work(port, tmp_path)


def test_production_rejects_symbol_only_foreign_and_rebound_sessions(tmp_path: Path) -> None:
    """Only one complete session owned by this port can bind."""
    port, _, _ = _prepared(tmp_path, lambda request, limit: _response(), bind_session=False)
    with pytest.raises(CoordinatedSessionError, match="intent_mismatch"):
        CoordinatedSession(exchange=port, expected_symbol=INTENT.symbol)
    with requests.Session() as foreign, pytest.raises(YahooProductionPortError, match="bind_rejected"):
        port.bind_session(foreign)
    session = CoordinatedSession(exchange=port, expected_symbol=INTENT.symbol, intent=INTENT)
    with pytest.raises(YahooProductionPortError, match="bind_rejected"):
        port.bind_session(session)
    with pytest.raises(YahooProductionPortError, match="bind_rejected"):
        CoordinatedSession(exchange=port, expected_symbol=INTENT.symbol, intent=INTENT)
    _assert_no_physical_work(port, tmp_path)


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"params": None},
        {"params": {**HISTORY_PARAMS, "period1": 1788188401}},
        {"params": {**HISTORY_PARAMS, "period2": 1788361201}},
        {"params": {**HISTORY_PARAMS, "period1": True}},
        {"params": {**HISTORY_PARAMS, "interval": "1h"}},
        {"params": {**HISTORY_PARAMS, "range": "1d"}},
        {"params": {**HISTORY_PARAMS, "includePrePost": 0}},
        {"params": {**HISTORY_PARAMS, "events": "div"}},
        {"params": {**HISTORY_PARAMS, "symbol": "1301.T"}},
        {"params": {**HISTORY_PARAMS, "crumb": ["secret-canary"]}},
        {"params": {"range": "1d", "interval": "1d", "period1": 0}},
        {"params": HISTORY_PARAMS, "data": "secret-canary"},
        {"params": HISTORY_PARAMS, "json": {}},
        {"params": HISTORY_PARAMS, "headers": {"Host": "secret-canary"}},
        {"params": HISTORY_PARAMS, "cookies": {"B": "secret-canary"}},
    ],
)
def test_chart_arguments_fail_before_any_physical_work(tmp_path: Path, kwargs: dict[str, object]) -> None:
    """Reject altered periods, options and body/header overrides before admission."""
    sends: list[object] = []
    port, _, _ = _prepared(tmp_path, lambda request, limit: sends.append(request))
    assert port._session is not None
    with pytest.raises(CoordinatedSessionError, match="chart_parameters") as caught:
        port._session.request("GET", REQUEST.url, **kwargs)
    assert "secret-canary" not in str(caught.value)
    assert sends == []
    _assert_no_physical_work(port, tmp_path)


@pytest.mark.parametrize("suffix", ["?period1=0", "?crumb=secret-canary", "/extra"])
def test_chart_url_cannot_override_bound_target(tmp_path: Path, suffix: str) -> None:
    """Target options must appear only in the validated params mapping."""
    port, _, _ = _prepared(tmp_path, lambda request, limit: pytest.fail("unexpected_send"))
    assert port._session is not None
    with pytest.raises(CoordinatedSessionError):
        port._session.get(REQUEST.url + suffix, params=HISTORY_PARAMS)
    _assert_no_physical_work(port, tmp_path)


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE logical_requests SET request_fingerprint = '" + "b" * 64 + "'",
        "UPDATE single_flight_consumers SET role = 'follower'",
        "UPDATE single_flight_consumers SET state = 'cancelled'",
        "UPDATE single_flight_consumers SET rate_domain = 'different-provider'",
        "UPDATE single_flights SET state = 'terminal'",
        "UPDATE single_flights SET leader_logical_request_id = 'different-leader'",
        "DELETE FROM single_flight_consumers",
    ],
)
def test_persisted_consumer_must_remain_the_active_leader(tmp_path: Path, sql: str) -> None:
    """Durable consumer identity cannot be replaced by caller-supplied gate keys."""
    sends: list[object] = []
    port, _, _ = _prepared(tmp_path, lambda request, limit: sends.append(request))
    with sqlite3.connect(tmp_path / ".runtime" / DATABASE_FILENAME) as connection:
        connection.execute(sql)
    with pytest.raises(RuntimeStorageError, match="consumer_mismatch"):
        _dispatch(port, IDENTITY, REQUEST)
    assert sends == []
    _assert_no_physical_work(port, tmp_path)


@pytest.mark.parametrize("copied", [True, False])
def test_copied_or_reentrant_envelope_is_rejected(
    tmp_path: Path, copied: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Object equality never replaces a one-time active dispatch capability."""
    port, _, _ = _prepared(tmp_path, lambda request, limit: pytest.fail("unexpected_send"))
    session = port._session
    assert session is not None
    original = port.exchange

    def intercept(identity: YahooRequestIdentity, envelope: EphemeralYahooRequest) -> YahooExchangeReceipt:
        if copied:
            envelope = EphemeralYahooRequest(envelope.method, envelope.url, dict(envelope.kwargs))
        else:
            session.assert_active_envelope(port, identity, envelope, consume=True)
        return original(identity, envelope)

    monkeypatch.setattr(port, "exchange", intercept)
    with pytest.raises(CoordinatedSessionError, match="exchange_failed"):
        session.get(REQUEST.url, params=HISTORY_PARAMS)
    _assert_no_physical_work(port, tmp_path)


def test_completed_envelope_cannot_be_replayed(tmp_path: Path) -> None:
    """A previously sent envelope becomes invalid after session dispatch returns."""
    seen: list[EphemeralYahooRequest] = []

    def backend(request: EphemeralYahooRequest, limit: None) -> object:
        seen.append(request)
        return _response()

    port, _, _ = _prepared(tmp_path, cast("YahooWireBackend", backend))
    _dispatch(port, IDENTITY, REQUEST)
    with pytest.raises(YahooProductionPortError, match="envelope_mismatch"):
        port.exchange(IDENTITY, seen[0])
    assert len(seen) == len(port.raw_references) == 1


def test_caller_parameter_mutation_cannot_change_dispatched_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nested caller-owned dictionaries are detached before admission."""
    params = {**HISTORY_PARAMS, "crumb": "ephemeral-canary"}
    port, _, _ = _prepared(tmp_path, lambda request, limit: _response())
    original = port.exchange

    def intercept(identity: YahooRequestIdentity, envelope: EphemeralYahooRequest) -> YahooExchangeReceipt:
        params["period1"] = 0
        assert envelope.kwargs["params"] == {**HISTORY_PARAMS, "crumb": "ephemeral-canary"}
        return original(identity, envelope)

    monkeypatch.setattr(port, "exchange", intercept)
    assert port._session is not None
    port._session.get(REQUEST.url, params=params)
    assert len(port.raw_references) == 1


@pytest.mark.parametrize("change", ["envelope", "consumer"])
def test_mutation_while_gate_waits_cannot_reach_backend(tmp_path: Path, change: str) -> None:
    """Recheck the admitted envelope after gate waits, before reserving another attempt."""
    current = [NOW + timedelta(seconds=2)]
    seen: list[object] = []

    def backend(request: EphemeralYahooRequest, limit: None) -> object:
        seen.append(request)
        return _response()

    def sleep(seconds: float) -> None:
        current[0] += timedelta(seconds=seconds)
        if change == "envelope":
            assert port._session is not None and port._session._active_request is not None
            params = cast("dict[str, object]", port._session._active_request.kwargs["params"])
            params["period1"] = 0
        else:
            with sqlite3.connect(tmp_path / ".runtime" / DATABASE_FILENAME) as connection:
                connection.execute("UPDATE single_flight_consumers SET state = 'cancelled'")

    port, _, _ = _prepared(tmp_path, cast("YahooWireBackend", backend), clock=lambda: current[0], sleep=sleep)
    _dispatch(port, IDENTITY, REQUEST)
    expected_error = CoordinatedSessionError if change == "envelope" else RuntimeStorageError
    with pytest.raises(expected_error, match="mismatch"):
        _dispatch(port, IDENTITY, REQUEST)
    assert len(seen) == len(port.raw_references) == 1


def test_binding_is_rechecked_immediately_before_backend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A change after reservation never reaches the wire backend."""
    port, repository, _ = _prepared(tmp_path, lambda request, limit: pytest.fail("unexpected_send"))
    original = repository.mark_physical_attempt_started

    def mutate(*args: Any, **kwargs: Any) -> None:
        original(*args, **kwargs)
        with sqlite3.connect(tmp_path / ".runtime" / DATABASE_FILENAME) as connection:
            connection.execute("UPDATE single_flight_consumers SET role = 'follower'")

    monkeypatch.setattr(repository, "mark_physical_attempt_started", mutate)
    with pytest.raises(YahooProductionPortError, match="outcome_unknown"):
        _dispatch(port, IDENTITY, REQUEST)
    assert port.raw_references == ()


def test_nested_session_dispatch_poisoning_prevents_outer_send(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Catching a nested-call rejection cannot authorize the outer envelope."""
    port, _, _ = _prepared(tmp_path, lambda request, limit: pytest.fail("unexpected_send"))
    session = port._session
    assert session is not None
    original = port.exchange

    def intercept(identity: YahooRequestIdentity, envelope: EphemeralYahooRequest) -> YahooExchangeReceipt:
        with pytest.raises(CoordinatedSessionError, match="session_poisoned"):
            session.get(REQUEST.url, params=HISTORY_PARAMS)
        return original(identity, envelope)

    monkeypatch.setattr(port, "exchange", intercept)
    with pytest.raises(CoordinatedSessionError, match="exchange_failed"):
        session.get(REQUEST.url, params=HISTORY_PARAMS)
    _assert_no_physical_work(port, tmp_path)


@pytest.mark.parametrize("change", ["task", "role", "period"])
def test_durable_fingerprint_cannot_authorize_a_different_consumer(tmp_path: Path, change: str) -> None:
    """A syntactically valid fingerprint must describe this exact requested binding."""
    port, _, _ = _prepared(tmp_path, lambda request, limit: pytest.fail("unexpected_send"))
    policy = compose_yahoo_production_policy(_reviewed_binding())
    changed_intent = INTENT.model_copy(update={"start": "2026-08-01"}) if change == "period" else INTENT
    fingerprint = policy.intent_fingerprint(
        changed_intent,
        task_id="task-other" if change == "task" else "task-yf",
        role=AgentRole.CLAUDE_WORKER if change == "role" else AgentRole.CODEX_WORKER,
    )
    with sqlite3.connect(tmp_path / ".runtime" / DATABASE_FILENAME) as connection:
        for table in ("logical_requests", "single_flight_consumers", "single_flights"):
            connection.execute(f"UPDATE {table} SET request_fingerprint = ?", (fingerprint,))
    with pytest.raises(RuntimeStorageError, match="consumer_mismatch"):
        _dispatch(port, IDENTITY, REQUEST)
    _assert_no_physical_work(port, tmp_path)


def test_live_opt_in_requires_private_runtime(tmp_path: Path) -> None:
    """A boolean alone never enables a production backend without cache/log isolation."""
    with pytest.raises(YahooProductionPortError, match="private_verification_required"):
        _prepared(tmp_path, None, live_verification=True)


def test_cookie_404_is_audited_while_logical_download_can_succeed(tmp_path: Path) -> None:
    """Preserve the real HTTP failure without failing the successful logical acquisition."""

    def backend(request: EphemeralYahooRequest, limit: None) -> object:
        cookie = request.url == "https://fc.yahoo.com"
        chart = "/chart/" in request.url
        response = _response(404 if cookie else 200, content_type="application/json" if chart else "text/plain")
        response.url = request.url
        return response

    port, repository, _ = _prepared(tmp_path, cast("YahooWireBackend", backend))
    cookie_receipt = _dispatch(
        port,
        YahooRequestIdentity("GET", "fc.yahoo.com", "cookie", "cookie_basic"),
        EphemeralYahooRequest("GET", "https://fc.yahoo.com", {"allow_redirects": False}),
    )
    assert cookie_receipt.status_code == 404 and cookie_receipt.raw_reference is None
    assert port.observations[0]["reason_code"] == "cookie_404_continue"
    assert repository.physical_attempt_state("attempt-1") == ("failed", 1)
    _dispatch(
        port,
        YahooRequestIdentity("GET", "query1.finance.yahoo.com", "crumb", "crumb_basic"),
        EphemeralYahooRequest("GET", "https://query1.finance.yahoo.com/v1/test/getcrumb", {"allow_redirects": False}),
    )
    _dispatch(port, IDENTITY, REQUEST)
    _dispatch(port, IDENTITY, REQUEST)
    assert port._session is not None and port._session.complete
    port._session.finalize(LogicalResultOutcome.SUCCEEDED)
    assert repository.logical_request_state("logical-yf") == "succeeded"


def test_live_capability_expires_with_private_runtime(tmp_path: Path) -> None:
    """An escaped port cannot send after cache/log isolation has ended."""
    with private_yfinance_runtime():
        port, _, _ = _prepared(tmp_path, None, live_verification=True)
    with pytest.raises(YahooProductionPortError, match="private_verification_required"):
        port.exchange(IDENTITY, REQUEST)
    _assert_no_physical_work(port, tmp_path)


def test_live_opt_in_rejects_injected_backend(tmp_path: Path) -> None:
    """An acceptance call cannot replace the controlled production backend."""
    with private_yfinance_runtime(), pytest.raises(YahooProductionPortError, match="private_verification_required"):
        _prepared(tmp_path, lambda request, limit: _response(), live_verification=True)
