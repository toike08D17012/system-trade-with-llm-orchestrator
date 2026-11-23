"""Fetch one operator-approved public issuer document through the durable request gate."""

import argparse
import ipaddress
import math
import os
import re
import socket
import stat
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from hashlib import sha256
from pathlib import Path
from typing import Literal, cast
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.external_requests import GateScope
from stock_research_llm_orchestrator.preparation.market_revalidation import _safe_path
from stock_research_llm_orchestrator.preparation.research_draft import DraftOutput, validate_bundle
from stock_research_llm_orchestrator.preparation.research_follow_up import (
    FollowUpIntake,
    build_follow_up,
    extract_text,
    validate_follow_up,
)
from stock_research_llm_orchestrator.preparation.storage import publish_preparation, read_bundle
from stock_research_llm_orchestrator.requests.production import (
    GateKeys,
    GateLimit,
    HierarchicalGatePolicy,
    LogicalResultOutcome,
    ProductionCachePolicy,
    ProductionLogicalRequest,
    ProductionPhysicalAttempt,
    QueuePolicy,
    RawPublicationIntent,
    RuntimeLeasePolicy,
)
from stock_research_llm_orchestrator.requests.raw_artifacts import RawArtifactPublisher
from stock_research_llm_orchestrator.requests.storage import RuntimeStorageError, initialize_runtime_storage
from stock_research_llm_orchestrator.requests.transport import (
    PhysicalTransportRequest,
    ProductionTransportCoordinator,
    TransportValidationPolicy,
    UntrustedTransportResponse,
)
from stock_research_llm_orchestrator.sources.protocol import CredentialFreeSourceIntent


_MEDIA_TYPES = ("text/html", "text/plain", "application/pdf")
_DESTINATION_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")


def validate_public_target(url: str, allowed_host: str) -> str:
    """Reject local, ambiguous or unapproved connection targets before a GET."""
    if any(ord(char) < 33 for char in url):
        raise ValueError("public_ir_url_invalid")
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        raise ValueError("public_ir_url_invalid") from None
    if (
        parsed.scheme != "https"
        or not host
        or host != allowed_host
        or not host.isascii()
        or parsed.username
        or parsed.password
        or port not in (None, 443)
        or parsed.fragment
    ):
        raise ValueError("public_ir_url_invalid")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("public_ir_ip_literal_forbidden")
    try:
        addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError:
        raise ValueError("public_ir_dns_failed") from None
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ValueError("public_ir_non_public_address")
    return host


def _retry_after(value: str | None, now: datetime) -> float | None:
    """Honor numeric and HTTP-date provider cooldowns without exposing response headers."""
    if value is None:
        return None
    try:
        delay = float(value)
    except ValueError:
        try:
            delay = (parsedate_to_datetime(value) - now).total_seconds()
        except ValueError, TypeError, OverflowError:
            return None
    return max(0.0, delay) if math.isfinite(delay) else None


@dataclass(frozen=True)
class PublicDocumentReceipt:
    """Retained original bytes and actual production transport identity."""

    body: bytes
    media_type: str
    retrieved_at: str
    physical_attempt_id: str
    sha256: str


def acquire_public_document(
    *,
    url: str,
    allowed_host: str,
    runtime: Path,
    runs: Path,
    task_id: str,
    source_id: str = "issuer-ir",
    operation: str = "follow-up-document",
    raw_schema_id: str = "issuer-ir-public-document-raw",
    approval_version: int = 1,
    media_types: tuple[str, ...] = _MEDIA_TYPES,
    profile_version: int = 1,
    audit: Callable[[dict[str, object]], None] | None = None,
    validate_body: Callable[[bytes, str], None] | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    transport: httpx.BaseTransport | None = None,
) -> PublicDocumentReceipt:
    """Fetch one public document and publish a replayable next-pass input."""
    host = validate_public_target(url, allowed_host)
    runtime, runs = _safe_path(runtime), _safe_path(runs)
    runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
    runs.mkdir(mode=0o700, parents=True, exist_ok=True)
    if runs.stat().st_uid != os.geteuid() or stat.S_IMODE(runs.stat().st_mode) != 0o700:
        raise ValueError("public_ir_runs_not_private")
    started = clock()
    uid = uuid4().hex
    intent = CredentialFreeSourceIntent(
        source_id=source_id,
        operation=operation,
        origin=host,
        resource_key=sha256(url.encode()).hexdigest(),
    )
    fingerprint = sha256((intent.model_dump_json() + url + source_id + "-public-fetch-v1").encode()).hexdigest()
    logical = ProductionLogicalRequest(
        logical_request_id=f"issuer-ir-{uid}",
        task_id=task_id,
        source_id=source_id,
        operation=operation,
        request_fingerprint=fingerprint,
        source_approval_version=approval_version,
        source_profile_version=profile_version,
        credential_scope_alias=None,
        egress_scope="default-egress",
        created_at=started.isoformat(),
    )
    repository = initialize_runtime_storage(runtime)
    lease = repository.acquire_lease(
        f"issuer-ir-owner-{uid}", started, RuntimeLeasePolicy(lease_duration_seconds=120, heartbeat_interval_seconds=30)
    )
    try:
        repository.admit_logical_request(
            logical, "issuer-ir", ProductionCachePolicy(applicable=False), lease, clock(), QueuePolicy()
        )
        claimed = repository.claim_next_queued("issuer-ir", lease, clock())
        if claimed is None or claimed.logical_request_id != logical.logical_request_id:
            raise ValueError("public_ir_request_not_claimed")
        attempt = ProductionPhysicalAttempt(
            physical_attempt_id=f"issuer-ir-attempt-{uid}",
            logical_request_id=logical.logical_request_id,
            sequence_number=1,
            lease_generation=lease.generation,
            created_at=clock().isoformat(),
        )
        received: list[datetime] = []
        if audit is not None:
            audit({"event": "logical_request", "logical_request": logical.model_dump(mode="json")})

        def send(request: PhysicalTransportRequest, _envelope: object) -> tuple[UntrustedTransportResponse, object]:
            if request != intent.to_transport_request(logical.logical_request_id, attempt.physical_attempt_id):
                raise ValueError("public_ir_request_mismatch")
            validate_public_target(url, allowed_host)
            if audit is not None:
                audit({"event": "transport_started", "physical_attempt_id": attempt.physical_attempt_id})
            try:
                with (
                    httpx.Client(
                        transport=transport,
                        follow_redirects=False,
                        trust_env=False,
                        timeout=httpx.Timeout(connect=10, read=30, write=10, pool=10),
                    ) as client,
                    client.stream("GET", url, headers={"Accept-Encoding": "identity"}) as response,
                ):
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                    media_type = response.headers.get("Content-Type", "").partition(";")[0].strip().lower()
                    status_code = response.status_code
                    retry_after_seconds = _retry_after(response.headers.get("Retry-After"), clock())
                    final_origin = response.url.host
                    redirected = response.is_redirect
            except httpx.HTTPError:
                raise RuntimeError("public_ir_transport_failed") from None
            received.append(clock())
            if audit is not None:
                audit(
                    {
                        "event": "transport_completed",
                        "status_code": status_code,
                        "physical_attempt_id": attempt.physical_attempt_id,
                    }
                )
            return (
                UntrustedTransportResponse(
                    status_code=status_code,
                    body=bytes(body),
                    media_type=media_type,
                    encoding="binary",
                    final_origin=final_origin,
                    redirected=redirected,
                    retry_after_seconds=retry_after_seconds,
                ),
                None,
            )

        def no_direct_send(_request: PhysicalTransportRequest) -> UntrustedTransportResponse:
            raise RuntimeError("public_ir_direct_send_forbidden")

        coordinator = ProductionTransportCoordinator(repository, no_direct_send, lambda: f"permit-{uuid4().hex}")
        try:
            exchange = coordinator.execute_exchange_with_callback(
                intent.to_transport_request(logical.logical_request_id, attempt.physical_attempt_id),
                attempt,
                f"reservation-{uid}",
                GateKeys(
                    egress="default-egress",
                    provider="public-web",
                    origin=host,
                    credential="anonymous",
                    operation=operation,
                    task=task_id,
                    role="source-acquisition",
                ),
                HierarchicalGatePolicy(
                    limits={
                        scope: GateLimit(
                            max_concurrency=1, min_interval_seconds=60, requests_per_window=1, window_seconds=60
                        )
                        for scope in GateScope
                    }
                ),
                TransportValidationPolicy(allowed_media_types=media_types, allowed_encodings=("binary",)),
                lease,
                clock(),
                clock,
                None,
                send,
            )
        except RuntimeStorageError as error:
            if audit is not None:
                audit(
                    {
                        "event": "gate_wait" if str(error).startswith("gate_blocked:") else "runtime_rejected",
                        "reason_code": str(error),
                        "retry_at": (clock() + timedelta(seconds=60)).timestamp()
                        if str(error).endswith((":minimum_interval", ":rolling_window"))
                        else None,
                    }
                )
            coordinator.finalize_logical_request(
                logical.logical_request_id, LogicalResultOutcome.FAILED, lease, clock(), "acquisition_gate_not_admitted"
            )
            raise
        result = exchange.execution
        if audit is not None:
            audit({"event": "gate_result", "status": result.status, "reason_code": result.reason_code})
        if result.status != "succeeded" or result.candidate is None:
            coordinator.finalize_logical_request(
                logical.logical_request_id, LogicalResultOutcome(result.status), lease, clock(), result.reason_code
            )
            raise ValueError(result.reason_code)
        candidate = result.candidate

        def check_body(body: bytes) -> None:
            if validate_body is None:
                extract_text(body, candidate.media_type)
            else:
                validate_body(body, candidate.media_type)

        try:
            RawArtifactPublisher(runs, repository).publish(
                candidate,
                RawPublicationIntent(
                    publication_id=f"issuer-ir-publication-{uid}",
                    task_id=task_id,
                    logical_request_id=logical.logical_request_id,
                    physical_attempt_id=attempt.physical_attempt_id,
                    source_id=source_id,
                    operation=operation,
                    content_sha256=candidate.sha256,
                    byte_count=len(candidate.body),
                    media_type=candidate.media_type,
                    encoding="binary",
                    raw_schema_id=raw_schema_id,
                    raw_schema_version=1,
                    publication_generation=lease.generation,
                ),
                lease,
                clock(),
                clock(),
                check_body,
            )
        except ValueError:
            coordinator.finalize_logical_request(
                logical.logical_request_id,
                LogicalResultOutcome.FAILED,
                lease,
                clock(),
                "public_ir_document_invalid",
            )
            raise
        coordinator.finalize_logical_request(
            logical.logical_request_id, LogicalResultOutcome.SUCCEEDED, lease, clock(), None
        )
        return PublicDocumentReceipt(
            candidate.body, candidate.media_type, received[0].isoformat(), attempt.physical_attempt_id, candidate.sha256
        )
    finally:
        repository.release_lease(lease, clock())


def acquire_public_ir_follow_up(
    *,
    prior: Path,
    url: str,
    allowed_host: str,
    request_index: int,
    runtime: Path,
    runs: Path,
    destination: str,
    task_id: str,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    transport: httpx.BaseTransport | None = None,
) -> Path:
    """Fetch one public document and publish a replayable legacy next-pass input."""
    previous = read_bundle(prior)
    validate_bundle(previous)
    requests = DraftOutput.model_validate_json(previous["draft.json"]).follow_up_requests
    if request_index < 0 or request_index >= len(requests):
        raise ValueError("follow_up_request_missing")
    if not _DESTINATION_NAME.fullmatch(destination):
        raise ValueError("public_ir_destination_invalid")
    if (runs / destination).exists():
        raise FileExistsError("public_ir_destination_exists")
    receipt = acquire_public_document(
        url=url,
        allowed_host=allowed_host,
        runtime=runtime,
        runs=runs,
        task_id=task_id,
        clock=clock,
        transport=transport,
    )
    intake = FollowUpIntake(
        request_index=request_index,
        source_id="issuer-ir",
        approval_version=1,
        source_url=url,
        retrieved_at=receipt.retrieved_at,
        media_type=cast(Literal["text/plain", "text/html", "application/pdf"], receipt.media_type),
    )
    files = build_follow_up(previous, intake, receipt.body)
    publish_preparation(runs, destination, files, validator=validate_follow_up)
    return runs / destination


def main(argv: list[str] | None = None) -> int:
    """Acquire one explicitly selected public issuer document."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-network", action="store_true", required=True)
    parser.add_argument("--prior", type=Path, required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--allow-host", required=True)
    parser.add_argument("--request-index", type=int, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--runs", type=Path, required=True)
    parser.add_argument("--destination", required=True)
    parser.add_argument("--task-id", required=True)
    args = parser.parse_args(argv)
    try:
        output = acquire_public_ir_follow_up(
            prior=args.prior,
            url=args.url,
            allowed_host=args.allow_host,
            request_index=args.request_index,
            runtime=args.runtime,
            runs=args.runs,
            destination=args.destination,
            task_id=args.task_id,
        )
        print(f"public_ir_follow_up: valid; next_input={output / 'input.json'}")
    except (ValueError, OSError, RuntimeError, RuntimeStorageError, KeyError, TypeError) as error:
        print(f"public_ir_follow_up_failed: {type(error).__name__}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
