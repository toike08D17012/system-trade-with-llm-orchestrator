"""Acquire one approved JPX workbook and share only a requested security's facts."""

import json
import os
import re
import stat
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import httpx
import yaml

from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.external_requests import GateScope
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.policies import SourceApprovalV1, SourceProfileV1
from stock_research_llm_orchestrator.preparation.market_evidence import (
    JPX_MEDIA_TYPE,
    JPX_WORKBOOK_URL,
    JpxSnapshotProvenance,
)
from stock_research_llm_orchestrator.preparation.market_revalidation import _safe_path
from stock_research_llm_orchestrator.preparation.research_draft import DraftInput, DraftOutput, validate_bundle
from stock_research_llm_orchestrator.preparation.research_follow_up import (
    FollowUpIntake,
    build_follow_up,
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
from stock_research_llm_orchestrator.requests.storage import initialize_runtime_storage
from stock_research_llm_orchestrator.requests.transport import (
    PhysicalTransportRequest,
    ProductionTransportCoordinator,
    TransportValidationPolicy,
    UntrustedTransportResponse,
)
from stock_research_llm_orchestrator.sources.jpx.current_list import JpxCurrentListAdapter
from stock_research_llm_orchestrator.sources.jpx.httpx_transport import JpxHttpClientPolicy, JpxPhysicalTransport
from stock_research_llm_orchestrator.sources.jpx.verification import verify_jpx_security
from stock_research_llm_orchestrator.sources.protocol import BoundedSourceResponse


_DESTINATION_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")


def select_jpx_request(files: Mapping[str, bytes]) -> int:
    """Select an explicit model request for the single approved workbook URL."""
    validate_bundle(files)
    output = DraftOutput.model_validate_json(files["draft.json"])
    for index, request in enumerate(output.follow_up_requests):
        if request.suggested_source_url == JPX_WORKBOOK_URL:
            return index
    raise ValueError("jpx_follow_up_request_missing")


def _check_approvals(root: Path, now: datetime) -> None:
    """Keep acquisition v1 and per-security LLM transfer v2 distinct."""
    if now.utcoffset() is None:
        raise ValueError("jpx_follow_up_clock_invalid")
    day = now.date().isoformat()
    acquisition: bytes | None = None
    for version in (1, 2):
        body = (root / "jpx" / f"v{version}.yaml").read_bytes()
        approval = SourceApprovalV1.model_validate(yaml.safe_load(body))
        if (
            approval.source_id != "jpx"
            or approval.approval_version != version
            or approval.status != "approved"
            or approval.effective_on is None
            or approval.effective_on > day
            or approval.recheck_due_on is None
            or day >= approval.recheck_due_on
            or (version == 1 and not approval.online_use_allowed)
            or (version == 2 and not approval.external_agent_transfer_allowed)
        ):
            raise ValueError("jpx_follow_up_source_not_approved")
        if version == 1:
            acquisition = body
    profile = SourceProfileV1.model_validate(yaml.safe_load((root.parent / "source-profiles/jpx/v1.yaml").read_bytes()))
    if (
        acquisition is None
        or profile.source_id != "jpx"
        or profile.profile_version != 1
        or not profile.enabled
        or profile.source_approval_status != "approved"
        or profile.source_approval_reference.sha256 != sha256(acquisition).hexdigest()
    ):
        raise ValueError("jpx_follow_up_profile_not_approved")


def _build(
    prior: Mapping[str, bytes], body: bytes, provenance: JpxSnapshotProvenance, request_index: int
) -> dict[str, bytes]:
    """Rebuild the per-security excerpt from exact retained workbook bytes."""
    validate_bundle(prior)
    data = DraftInput.model_validate_json(prior["input.json"])
    output = DraftOutput.model_validate_json(prior["draft.json"])
    if request_index < 0 or request_index >= len(output.follow_up_requests):
        raise ValueError("jpx_follow_up_request_missing")
    if output.follow_up_requests[request_index].suggested_source_url != JPX_WORKBOOK_URL:
        raise ValueError("jpx_follow_up_url_invalid")
    if sha256(body).hexdigest() != provenance.sha256:
        raise ValueError("jpx_follow_up_hash_mismatch")
    snapshot = JpxCurrentListAdapter().parse(
        BoundedSourceResponse(
            physical_attempt_id=provenance.physical_attempt_id,
            body=body,
            sha256=provenance.sha256,
            media_type=JPX_MEDIA_TYPE,
            encoding="binary",
        )
    )
    verification = verify_jpx_security(snapshot, data.security_code)
    excerpt = json.dumps(
        {
            "verification": verification.model_dump(mode="json"),
            "workbook_sha256": provenance.sha256,
            "retrieved_at": provenance.retrieved_at,
            "limitation": "The snapshot date does not establish current listing status after that date.",
        },
        ensure_ascii=False,
        sort_keys=True,
    ).encode()
    intake = FollowUpIntake(
        request_index=request_index,
        source_id="jpx",
        approval_version=2,
        source_url=JPX_WORKBOOK_URL,
        retrieved_at=provenance.retrieved_at,
        media_type="text/plain",
    )
    files = build_follow_up(prior, intake, excerpt)
    return {
        **files,
        "jpx-workbook.xlsx": body,
        "jpx-provenance.json": provenance.model_dump_json(indent=2).encode() + b"\n",
    }


def validate_jpx_follow_up(files: Mapping[str, bytes]) -> None:
    """Replay the next input and excerpt from retained official workbook bytes."""
    core = {name: body for name, body in files.items() if name not in {"jpx-workbook.xlsx", "jpx-provenance.json"}}
    validate_follow_up(core)
    prior = {
        "input.json": files["prior-input.json"],
        "draft.json": files["prior-draft.json"],
        "manifest.json": files["prior-manifest.json"],
    }
    intake = FollowUpIntake.model_validate_json(files["intake.json"])
    provenance = JpxSnapshotProvenance.model_validate_json(files["jpx-provenance.json"])
    if intake.source_id != "jpx" or intake.approval_version != 2 or intake.source_url != JPX_WORKBOOK_URL:
        raise ValueError("jpx_follow_up_intake_invalid")
    if dict(files) != _build(prior, files["jpx-workbook.xlsx"], provenance, intake.request_index):
        raise ValueError("jpx_follow_up_binding_invalid")


def acquire_jpx_follow_up(
    *,
    prior: Path,
    request_index: int,
    runtime: Path,
    runs: Path,
    destination: str,
    task_id: str,
    approval_root: Path = Path("config/source-approvals"),
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    transport: httpx.BaseTransport | None = None,
) -> Path:
    """Fetch one fixed JPX workbook through the durable gate and publish a next-pass input."""
    previous = read_bundle(prior)
    if select_jpx_request(previous) != request_index:
        raise ValueError("jpx_follow_up_request_mismatch")
    security = DraftInput.model_validate_json(previous["input.json"])
    if security.exchange != "XTKS" or not re.fullmatch(r"[0-9A-Z]{4}", security.security_code):
        raise ValueError("jpx_follow_up_security_invalid")
    if not _DESTINATION_NAME.fullmatch(destination):
        raise ValueError("jpx_follow_up_destination_invalid")
    started = clock()
    _check_approvals(approval_root, started)
    runtime, runs = _safe_path(runtime), _safe_path(runs)
    runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
    runs.mkdir(mode=0o700, parents=True, exist_ok=True)
    if runs.stat().st_uid != os.geteuid() or stat.S_IMODE(runs.stat().st_mode) != 0o700:
        raise ValueError("jpx_follow_up_runs_not_private")
    if (runs / destination).exists():
        raise FileExistsError("jpx_follow_up_destination_exists")
    uid = uuid4().hex
    adapter = JpxCurrentListAdapter()
    intent = adapter.build_intent("current-listed-issues", ())
    logical = ProductionLogicalRequest(
        logical_request_id=f"jpx-{uid}",
        task_id=task_id,
        source_id="jpx",
        operation="current-listed-issues",
        request_fingerprint=sha256(intent.model_dump_json().encode()).hexdigest(),
        source_approval_version=1,
        source_profile_version=1,
        credential_scope_alias=None,
        egress_scope="default-egress",
        created_at=started.isoformat(),
    )
    repository = initialize_runtime_storage(runtime)
    lease = repository.acquire_lease(
        f"jpx-owner-{uid}", started, RuntimeLeasePolicy(lease_duration_seconds=120, heartbeat_interval_seconds=30)
    )
    try:
        repository.admit_logical_request(
            logical, "jpx-public-web", ProductionCachePolicy(applicable=False), lease, clock(), QueuePolicy()
        )
        claimed = repository.claim_next_queued("jpx-public-web", lease, clock())
        if claimed is None or claimed.logical_request_id != logical.logical_request_id:
            raise ValueError("jpx_follow_up_request_not_claimed")
        attempt = ProductionPhysicalAttempt(
            physical_attempt_id=f"jpx-attempt-{uid}",
            logical_request_id=logical.logical_request_id,
            sequence_number=1,
            lease_generation=lease.generation,
            created_at=clock().isoformat(),
        )
        send = JpxPhysicalTransport(
            intent, JpxHttpClientPolicy(connect_timeout_seconds=10, read_timeout_seconds=30), transport
        )

        received: list[datetime] = []

        def callback(request: PhysicalTransportRequest, _envelope: object) -> tuple[UntrustedTransportResponse, object]:
            response = send(request)
            received.append(clock())
            return response, None

        def no_direct_send(_request: PhysicalTransportRequest) -> UntrustedTransportResponse:
            raise RuntimeError("jpx_direct_send_forbidden")

        coordinator = ProductionTransportCoordinator(repository, no_direct_send, lambda: f"permit-{uuid4().hex}")
        exchange = coordinator.execute_exchange_with_callback(
            intent.to_transport_request(logical.logical_request_id, attempt.physical_attempt_id),
            attempt,
            f"reservation-{uid}",
            GateKeys(
                egress="default-egress",
                provider="jpx-public-web",
                origin="www.jpx.co.jp",
                credential="anonymous",
                operation="current-listed-issues",
                task=task_id,
                role="source-acquisition",
            ),
            HierarchicalGatePolicy(
                limits={
                    scope: GateLimit(
                        max_concurrency=1, min_interval_seconds=60, requests_per_window=3, window_seconds=86400
                    )
                    for scope in GateScope
                }
            ),
            TransportValidationPolicy(allowed_media_types=(JPX_MEDIA_TYPE,), allowed_encodings=("binary",)),
            lease,
            clock(),
            clock,
            None,
            callback,
        )
        result = exchange.execution
        if result.status != "succeeded" or result.candidate is None:
            coordinator.finalize_logical_request(
                logical.logical_request_id, LogicalResultOutcome(result.status), lease, clock(), result.reason_code
            )
            raise ValueError(result.reason_code)
        candidate = result.candidate

        def validate_body(body: bytes) -> None:
            adapter.parse(
                BoundedSourceResponse(
                    physical_attempt_id=attempt.physical_attempt_id,
                    body=body,
                    sha256=candidate.sha256,
                    media_type=candidate.media_type,
                    encoding=candidate.encoding,
                )
            )

        RawArtifactPublisher(runs, repository).publish(
            candidate,
            RawPublicationIntent(
                publication_id=f"jpx-publication-{uid}",
                task_id=task_id,
                logical_request_id=logical.logical_request_id,
                physical_attempt_id=attempt.physical_attempt_id,
                source_id="jpx",
                operation="current-listed-issues",
                content_sha256=candidate.sha256,
                byte_count=len(candidate.body),
                media_type=candidate.media_type,
                encoding=candidate.encoding,
                raw_schema_id="jpx-current-listed-issues-xlsx",
                raw_schema_version=1,
                publication_generation=lease.generation,
            ),
            lease,
            clock(),
            clock(),
            validate_body,
        )
        provenance = JpxSnapshotProvenance(
            physical_attempt_id=attempt.physical_attempt_id,
            retrieved_at=received[0].isoformat(),
            sha256=candidate.sha256,
        )
        files = _build(previous, candidate.body, provenance, request_index)
        publish_preparation(runs, destination, files, validator=validate_jpx_follow_up)
        coordinator.finalize_logical_request(
            logical.logical_request_id, LogicalResultOutcome.SUCCEEDED, lease, clock(), None
        )
        return runs / destination
    finally:
        repository.release_lease(lease, clock())
