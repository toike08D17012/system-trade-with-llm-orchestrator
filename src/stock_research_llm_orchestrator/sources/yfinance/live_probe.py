"""Opt-in auxiliary HTTP verification; never prints or persists provider bodies."""

import argparse
import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from time import sleep

from curl_cffi import requests

from stock_research_llm_orchestrator.configuration import validate_configuration
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.research import AgentRole
from stock_research_llm_orchestrator.requests.models import SourceBinding, bind_source
from stock_research_llm_orchestrator.requests.production import LogicalResultOutcome, QueuePolicy, RuntimeLeasePolicy
from stock_research_llm_orchestrator.requests.storage import initialize_runtime_storage
from stock_research_llm_orchestrator.requests.transport import (
    PhysicalTransportRequest,
    ProductionTransportCoordinator,
    ReceivedResponseValidationError,
    UntrustedTransportResponse,
)
from stock_research_llm_orchestrator.sources.yfinance.coordinated_session import (
    EphemeralYahooRequest,
    YahooRequestIdentity,
)
from stock_research_llm_orchestrator.sources.yfinance.private_runtime import private_yfinance_runtime
from stock_research_llm_orchestrator.sources.yfinance.production_policy import compose_yahoo_production_policy
from stock_research_llm_orchestrator.sources.yfinance.production_port import CurlCffiYahooBackend, _normalize_response


def run_probe(output: Path, *, allow_network: bool = False) -> dict[str, object]:
    """Check cookie then crumb via durable gates, stopping at the first failed exchange."""
    if allow_network is not True:
        raise ValueError("explicit_network_opt_in_required")

    def now() -> datetime:
        return datetime.now(UTC)

    binding = bind_source(validate_configuration(Path("config")), "yfinance", 3, 3, now().date())
    if not isinstance(binding, SourceBinding):
        raise ValueError("yahoo_source_binding_rejected")
    policy = compose_yahoo_production_policy(binding)
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    runtime = output / ".runtime"
    runtime.mkdir(mode=0o700)
    repository = initialize_runtime_storage(runtime)
    lease_policy = RuntimeLeasePolicy()
    lease = repository.acquire_lease("yahoo-probe-owner", now(), lease_policy)
    task = "yahoo-auxiliary-verification"
    logical = policy.logical_request(
        logical_request_id="yahoo-auxiliary-probe",
        task_id=task,
        request_fingerprint=hashlib.sha256(b"yahoo-auxiliary-verification-v1").hexdigest(),
        created_at=now(),
    )
    repository.admit_logical_request(logical, policy.rate_domain, policy.cache_policy(), lease, now(), QueuePolicy())
    if repository.claim_next_queued(policy.rate_domain, lease, now()) is None:
        raise ValueError("yahoo_probe_queue_not_claimed")
    observations: list[dict[str, object]] = []
    summary: dict[str, object] = {
        "probe": "auxiliary-http-only",
        "started_at": now().isoformat(),
        "observations": observations,
        "body_retained": False,
        "automatic_retry": False,
        "status": "failed",
        "full_download_verified": False,
    }

    def forbidden(request: PhysicalTransportRequest) -> UntrustedTransportResponse:
        raise RuntimeError("uncontrolled_transport_rejected")

    ids = iter(range(1, 100))
    coordinator = ProductionTransportCoordinator(repository, forbidden, lambda: f"permit-{next(ids)}")
    outcome = LogicalResultOutcome.FAILED
    reason = "probe_incomplete"
    try:
        with private_yfinance_runtime(), requests.Session(trust_env=False) as session:
            session.retry.count = 0
            backend = CurlCffiYahooBackend()
            backend.bind_session(session)
            targets = (
                (YahooRequestIdentity("GET", "fc.yahoo.com", "cookie", "cookie_basic"), "https://fc.yahoo.com"),
                (
                    YahooRequestIdentity("GET", "query1.finance.yahoo.com", "crumb", "crumb_basic"),
                    "https://query1.finance.yahoo.com/v1/test/getcrumb",
                ),
            )
            for index, (identity, url) in enumerate(targets):
                if index:
                    sleep(binding.limits()[1])
                lease = repository.heartbeat_lease(lease, now(), lease_policy)
                repository.assert_claimed_logical_request(
                    logical.logical_request_id,
                    task_id=task,
                    source_id="yfinance",
                    operation="daily-history",
                    source_approval_version=2,
                    source_profile_version=3,
                    credential_scope_alias=None,
                    egress_scope=binding.profile.egress_scope,
                    rate_domain=policy.rate_domain,
                    lease=lease,
                    now=now(),
                    request_fingerprint=logical.request_fingerprint,
                )
                attempt = repository.reserve_next_physical_attempt(
                    f"attempt-{index}", logical.logical_request_id, lease, now()
                )
                request = EphemeralYahooRequest("GET", url, {"allow_redirects": False, "timeout": 10})
                observation: dict[str, object] = {
                    "operation": identity.endpoint,
                    "physical_attempt_id": attempt.physical_attempt_id,
                    "logical_request_id": logical.logical_request_id,
                    "validation": "not_received",
                }
                observations.append(observation)

                def send(
                    physical: PhysicalTransportRequest,
                    envelope: object,
                    *,
                    request: EphemeralYahooRequest = request,
                    identity: YahooRequestIdentity = identity,
                    observation: dict[str, object] = observation,
                ) -> tuple[UntrustedTransportResponse, object]:
                    if envelope is not request:
                        raise RuntimeError("probe_envelope_mismatch")
                    try:
                        response = backend(request, None)
                    except Exception as error:
                        error_type = type(error).__name__
                        observation["transport_error"] = (
                            error_type
                            if error_type in {"DNSError", "ConnectionError", "Timeout", "ProxyError", "SSLError"}
                            else "TransportError"
                        )
                        raise
                    status = getattr(response, "status_code", None)
                    body = getattr(response, "content", None)
                    headers = getattr(response, "headers", {})
                    media = str(headers.get("Content-Type", "")).split(";", 1)[0].lower().strip()
                    observation.update(
                        retrieved_at=now().isoformat(),
                        status_code=status if type(status) is int else None,
                        byte_count=len(body) if isinstance(body, bytes) else None,
                        media_type=media if media in {"text/html", "text/plain"} else "unapproved",
                        validation="failed",
                    )
                    try:
                        normalized = _normalize_response(response, request, identity, now())
                    except ReceivedResponseValidationError as error:
                        observation["validation_error"] = str(error)
                        raise
                    observation.update(validation="passed", encoding="utf-8")
                    return normalized, response

                result = coordinator.execute_exchange_with_callback(
                    PhysicalTransportRequest(
                        logical_request_id=logical.logical_request_id,
                        physical_attempt_id=attempt.physical_attempt_id,
                        origin=identity.origin,
                        operation=identity.endpoint,
                        resource_key=identity.resource_class,
                    ),
                    attempt,
                    f"reservation-{index}",
                    policy.gate_keys(identity, task_id=task, role=AgentRole.ORCHESTRATOR),
                    policy.gate_policy(),
                    policy.transport_policy_for(identity),
                    lease,
                    now(),
                    now,
                    request,
                    send,
                )
                reason = result.execution.reason_code
                observation["reason_code"] = reason
                cookie_not_found = (
                    identity.endpoint == "cookie_basic"
                    and result.status_code == 404
                    and result.execution.candidate is not None
                    and observation["validation"] == "passed"
                )
                if cookie_not_found:
                    observation["reason_code"] = "cookie_404_continue"
                if result.execution.status != "succeeded" and not cookie_not_found:
                    outcome = (
                        LogicalResultOutcome.UNKNOWN
                        if result.execution.status == "unknown"
                        else LogicalResultOutcome.FAILED
                    )
                    break
            else:
                outcome, reason = LogicalResultOutcome.SUCCEEDED, "ok"
                summary["status"] = "passed"
            session.cookies.clear()
    except Exception:
        reason = "probe_control_failure"
    finally:
        try:
            coordinator.finalize_logical_request(
                logical.logical_request_id,
                outcome,
                lease,
                now(),
                None if outcome is LogicalResultOutcome.SUCCEEDED else reason,
            )
            repository.release_lease(lease, now())
        except Exception:
            summary["status"] = "failed"
            reason = "probe_finalization_failed"
        summary.update(completed_at=now().isoformat(), reason_code=reason)
        with (output / "summary.json").open("x", encoding="utf-8") as stream:
            os.chmod(output / "summary.json", 0o600)
            json.dump(summary, stream, ensure_ascii=True, indent=2)
    return summary


def main() -> None:
    """Require an explicit network flag and a fresh private output directory."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-network", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        summary = run_probe(args.output, allow_network=args.allow_network)
    except Exception:
        print("yahoo_probe_setup_failed")
        raise SystemExit(1) from None
    print(json.dumps(summary, ensure_ascii=True, indent=2))
    raise SystemExit(0 if summary["status"] == "passed" else 1)


if __name__ == "__main__":
    main()
