"""One approved public EDINET code-list acquisition with durable raw provenance."""

import json
import os
import stat
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import httpx

from stock_research_llm_orchestrator.configuration import validate_configuration
from stock_research_llm_orchestrator.contracts.base import StrictContractModel
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.external_requests import GateScope
from stock_research_llm_orchestrator.preparation.market_revalidation import _safe_path, _timestamp
from stock_research_llm_orchestrator.preparation.storage import publish_preparation
from stock_research_llm_orchestrator.requests.models import SourceBinding, bind_source
from stock_research_llm_orchestrator.requests.production import (
    CommittedRawReference,
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
from stock_research_llm_orchestrator.requests.storage import (
    DATABASE_FILENAME,
    ProductionRequestRepository,
    initialize_runtime_storage,
)
from stock_research_llm_orchestrator.requests.transport import (
    PhysicalTransportRequest,
    ProductionTransportCoordinator,
    ReceivedResponseValidationError,
    TransportValidationPolicy,
    UntrustedTransportResponse,
)
from stock_research_llm_orchestrator.sources.edinet.code_list import (
    CODE_LIST_URL,
    MAX_ZIP_BYTES,
    CodeListObservation,
    parse_code_list,
)
from stock_research_llm_orchestrator.sources.protocol import CredentialFreeSourceIntent


APPROVAL_SHA256 = "cedb9863d358232434dc1691fecb40ec4fd3032f0b2db3e91e562a6ce6e24a1d"
PROFILE_SHA256 = "7f21e11dd9b5d13c4aec2fff6c7bc7bb6cd25051db4f9bf0a74c29ef3b25532d"
ORIGIN = "disclosure2dl.edinet-fsa.go.jp"


class CodeListReceipt(StrictContractModel):
    """Approved request and uniquely parsed issuer tied to committed raw bytes."""

    requested_at: str
    received_at: str
    recorded_at: str
    url: str
    observation: CodeListObservation
    raw_reference: CommittedRawReference


def _load_binding(config: Path, at: datetime) -> None:
    for relative, digest in (
        ("source-approvals/edinet-code-list/v1.yaml", APPROVAL_SHA256),
        ("source-profiles/edinet-code-list/v1.yaml", PROFILE_SHA256),
    ):
        path = config / relative
        if any(part.is_symlink() for part in (path, *path.parents)) or sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("edinet_code_list_configuration_changed")
    binding = bind_source(
        validate_configuration(config),
        source_id="edinet-code-list",
        profile_version=1,
        policy_version=1,
        evaluation_date=at.date(),
    )
    if not isinstance(binding, SourceBinding):
        raise ValueError("edinet_code_list_binding_unavailable")
    if (
        binding.approval_reference.sha256 != APPROVAL_SHA256
        or binding.profile_reference.sha256 != PROFILE_SHA256
        or binding.approval.credential_scope_alias is not None
        or binding.profile.credential_scope_alias is not None
        or binding.approval.external_agent_transfer_allowed
        or binding.profile.cache_policy.enabled
        or binding.profile.cooldown_policy.automatic_retry_allowed
        or binding.profile.direct_connection_fallback_allowed
        or binding.limits()[:5] != (1, 60, 1, 60, 1)
    ):
        raise ValueError("edinet_code_list_binding_invalid")


def _intent() -> CredentialFreeSourceIntent:
    return CredentialFreeSourceIntent(
        source_id="edinet-code-list",
        operation="code-list-zip",
        origin=ORIGIN,
        resource_key="edinetcode",
    )


def _send_once(
    request: PhysicalTransportRequest, received: list[datetime], clock: Callable[[], datetime]
) -> UntrustedTransportResponse:
    """Perform one anonymous fixed-URL GET after the coordinator issues a permit."""
    if request.origin != ORIGIN or request.operation != "code-list-zip" or request.resource_key != "edinetcode":
        raise ValueError("edinet_code_list_target_invalid")
    try:
        with (
            httpx.Client(timeout=httpx.Timeout(30), follow_redirects=False, trust_env=False) as client,
            client.stream("GET", CODE_LIST_URL, headers={"Accept-Encoding": "identity"}) as response,
        ):
            if response.is_redirect:
                raise ReceivedResponseValidationError("edinet_code_list_redirect")
            body = bytearray()
            for chunk in response.iter_bytes():
                body.extend(chunk)
                if len(body) > MAX_ZIP_BYTES:
                    raise ReceivedResponseValidationError("edinet_code_list_oversized")
            received.append(clock())
            return UntrustedTransportResponse(
                status_code=response.status_code,
                body=bytes(body),
                media_type=response.headers.get("Content-Type", "").partition(";")[0].strip().lower(),
                encoding="binary",
                final_origin=response.url.host,
                redirected=False,
            )
    except ReceivedResponseValidationError:
        raise
    except Exception:
        raise RuntimeError("edinet_code_list_http_failed") from None


def validate_code_list_receipt(files: Mapping[str, bytes], *, runtime: Path, runs: Path) -> CodeListReceipt:
    """Replay exact approved bytes and compare the committed runtime publication."""
    required = {"approval.yaml", "profile.yaml", "receipt.json"}
    if set(files) != required | {"index.json"} or json.loads(files["index.json"]) != {
        name: sha256(files[name]).hexdigest() for name in sorted(required)
    }:
        raise ValueError("edinet_code_list_receipt_inventory_invalid")
    if (
        sha256(files["approval.yaml"]).hexdigest() != APPROVAL_SHA256
        or sha256(files["profile.yaml"]).hexdigest() != PROFILE_SHA256
    ):
        raise ValueError("edinet_code_list_receipt_policy_invalid")
    receipt = CodeListReceipt.model_validate_json(files["receipt.json"])
    requested, received, recorded = map(_timestamp, (receipt.requested_at, receipt.received_at, receipt.recorded_at))
    reference = receipt.raw_reference
    if (
        receipt.url != CODE_LIST_URL
        or not requested <= received <= recorded
        or requested.date().isoformat() != "2026-09-30"
        or (reference.raw_schema_id, reference.raw_schema_version) != ("edinet-code-list-zip-raw", 1)
    ):
        raise ValueError("edinet_code_list_receipt_scope_invalid")
    database = _safe_path(runtime / DATABASE_FILENAME)
    if not database.is_file():
        raise ValueError("edinet_code_list_runtime_missing")
    repo = ProductionRequestRepository(database)
    logical = repo.logical_request(reference.logical_request_id)
    identity = repo.physical_attempt_identity(reference.physical_attempt_id)
    publication = repo.raw_publication_record(reference.publication_id)
    fingerprint = sha256((_intent().model_dump_json() + APPROVAL_SHA256 + PROFILE_SHA256).encode()).hexdigest()
    if (
        logical is None
        or (logical.task_id, logical.source_id, logical.operation, logical.created_at, logical.request_fingerprint)
        != (reference.task_id, "edinet-code-list", "code-list-zip", receipt.requested_at, fingerprint)
        or identity is None
        or identity[0] != reference.logical_request_id
        or identity[2] != reference.publication_generation
        or repo.physical_attempt_state(reference.physical_attempt_id) != ("succeeded", reference.publication_generation)
        or publication is None
        or publication.state != "committed"
        or publication.intent.source_id != "edinet-code-list"
        or publication.intent.operation != "code-list-zip"
        or reference not in repo.committed_raw_references(reference.logical_request_id)
    ):
        raise ValueError("edinet_code_list_runtime_mismatch")
    raw = _safe_path(runs / reference.task_id / reference.relative_path)
    body = (raw / "body.bin").read_bytes()
    if sha256(body).hexdigest() != reference.content_sha256 or len(body) != reference.byte_count:
        raise ValueError("edinet_code_list_raw_mismatch")
    request = json.loads((raw / "request.json").read_bytes())
    response = json.loads((raw / "response.json").read_bytes())
    saved = json.loads((raw / "receipt.json").read_bytes())
    if (
        request
        != {
            key: getattr(reference, key)
            for key in (
                "publication_id",
                "task_id",
                "logical_request_id",
                "physical_attempt_id",
                "publication_generation",
            )
        }
        | {"source_id": "edinet-code-list", "operation": "code-list-zip"}
        or response
        != {
            "byte_count": reference.byte_count,
            "media_type": publication.intent.media_type,
            "encoding": publication.intent.encoding,
            "raw_schema_id": reference.raw_schema_id,
            "raw_schema_version": reference.raw_schema_version,
        }
        or saved != {"body_sha256": reference.content_sha256, "byte_count": reference.byte_count}
        or parse_code_list(body, received.date()) != receipt.observation
    ):
        raise ValueError("edinet_code_list_raw_receipt_mismatch")
    return receipt


def validate_embedded_code_list(files: Mapping[str, bytes]) -> CodeListReceipt:
    """Replay a self-contained copy of one already committed receipt for financial inputs."""
    required = {
        "approval.yaml",
        "profile.yaml",
        "receipt.json",
        "index.json",
        "raw/body.bin",
        "raw/request.json",
        "raw/response.json",
        "raw/receipt.json",
    }
    if set(files) != required:
        raise ValueError("edinet_code_list_embedded_inventory_invalid")
    metadata = {name: files[name] for name in ("approval.yaml", "profile.yaml", "receipt.json", "index.json")}
    expected_index = {
        name: sha256(metadata[name]).hexdigest() for name in ("approval.yaml", "profile.yaml", "receipt.json")
    }
    if (
        json.loads(metadata["index.json"]) != expected_index
        or sha256(metadata["approval.yaml"]).hexdigest() != APPROVAL_SHA256
        or sha256(metadata["profile.yaml"]).hexdigest() != PROFILE_SHA256
    ):
        raise ValueError("edinet_code_list_embedded_policy_invalid")
    receipt = CodeListReceipt.model_validate_json(metadata["receipt.json"])
    reference = receipt.raw_reference
    requested, received, recorded = map(_timestamp, (receipt.requested_at, receipt.received_at, receipt.recorded_at))
    if (
        receipt.url != CODE_LIST_URL
        or requested.date().isoformat() != "2026-09-30"
        or not requested <= received <= recorded
        or (reference.raw_schema_id, reference.raw_schema_version) != ("edinet-code-list-zip-raw", 1)
    ):
        raise ValueError("edinet_code_list_embedded_scope_invalid")
    body = files["raw/body.bin"]
    if sha256(body).hexdigest() != reference.content_sha256 or len(body) != reference.byte_count:
        raise ValueError("edinet_code_list_embedded_raw_mismatch")
    request = json.loads(files["raw/request.json"])
    response = json.loads(files["raw/response.json"])
    saved_receipt = json.loads(files["raw/receipt.json"])
    if (
        request
        != {
            key: getattr(reference, key)
            for key in (
                "publication_id",
                "task_id",
                "logical_request_id",
                "physical_attempt_id",
                "publication_generation",
            )
        }
        | {"source_id": "edinet-code-list", "operation": "code-list-zip"}
        or response
        not in (
            {
                "byte_count": reference.byte_count,
                "media_type": media_type,
                "encoding": "binary",
                "raw_schema_id": reference.raw_schema_id,
                "raw_schema_version": reference.raw_schema_version,
            }
            for media_type in ("application/octet-stream", "application/zip")
        )
        or saved_receipt != {"body_sha256": reference.content_sha256, "byte_count": reference.byte_count}
        or parse_code_list(body, received.date()) != receipt.observation
    ):
        raise ValueError("edinet_code_list_embedded_receipt_mismatch")
    return receipt


def acquire_code_list(
    *,
    config: Path,
    runtime: Path,
    runs: Path,
    destination: str,
    task_id: str,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> CodeListReceipt:
    """Acquire only the fixed public ZIP with one physical attempt and no retry."""
    config, runtime, runs = map(_safe_path, (config, runtime, runs))
    if not destination.isascii() or not destination.replace("-", "").replace("_", "").isalnum():
        raise ValueError("edinet_code_list_destination_invalid")
    if (runs / destination).exists():
        raise FileExistsError("edinet_code_list_destination_exists")
    now = clock()
    _load_binding(config, now)
    runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
    runs.mkdir(mode=0o700, parents=True, exist_ok=True)
    if runs.stat().st_uid != os.geteuid() or stat.S_IMODE(runs.stat().st_mode) != 0o700:
        raise ValueError("edinet_code_list_runs_not_private")
    uid = uuid4().hex
    intent = _intent()
    fingerprint = sha256((intent.model_dump_json() + APPROVAL_SHA256 + PROFILE_SHA256).encode()).hexdigest()
    logical = ProductionLogicalRequest(
        logical_request_id="edinet-code-list-20260930",
        task_id=task_id,
        source_id="edinet-code-list",
        operation="code-list-zip",
        request_fingerprint=fingerprint,
        source_approval_version=1,
        source_profile_version=1,
        credential_scope_alias=None,
        egress_scope="default-egress",
        created_at=now.isoformat(),
    )
    repo = initialize_runtime_storage(runtime)
    lease = repo.acquire_lease(
        f"edinet-code-list-owner-{uid}",
        now,
        RuntimeLeasePolicy(lease_duration_seconds=120, heartbeat_interval_seconds=30),
    )
    try:
        repo.admit_logical_request(
            logical, "edinet-code-list", ProductionCachePolicy(applicable=False), lease, clock(), QueuePolicy()
        )
        claimed = repo.claim_next_queued("edinet-code-list", lease, clock())
        if claimed is None or claimed.logical_request_id != logical.logical_request_id:
            raise ValueError("edinet_code_list_request_not_claimed")
        attempt = ProductionPhysicalAttempt(
            physical_attempt_id=f"edinet-code-list-attempt-{uid}",
            logical_request_id=logical.logical_request_id,
            sequence_number=1,
            lease_generation=lease.generation,
            created_at=clock().isoformat(),
        )
        received: list[datetime] = []

        def send(request: PhysicalTransportRequest, _envelope: object) -> tuple[UntrustedTransportResponse, object]:
            try:
                _load_binding(config, clock())
            except ValueError:
                raise ReceivedResponseValidationError("edinet_code_list_binding_changed") from None
            return _send_once(request, received, clock), None

        def no_direct_send(_request: PhysicalTransportRequest) -> UntrustedTransportResponse:
            raise RuntimeError("no_direct_send")

        coordinator = ProductionTransportCoordinator(repo, no_direct_send, lambda: f"permit-{uuid4().hex}")
        exchange = coordinator.execute_exchange_with_callback(
            intent.to_transport_request(logical.logical_request_id, attempt.physical_attempt_id),
            attempt,
            f"reservation-{uid}",
            GateKeys(
                egress="default-egress",
                provider="edinet-code-list",
                origin=ORIGIN,
                credential="anonymous",
                operation="code-list-zip",
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
            TransportValidationPolicy(
                allowed_media_types=("application/octet-stream", "application/zip"),
                allowed_encodings=("binary",),
            ),
            lease,
            clock(),
            clock,
            None,
            send,
        )
        result = exchange.execution
        if result.status != "succeeded" or result.candidate is None:
            coordinator.finalize_logical_request(
                logical.logical_request_id, LogicalResultOutcome(result.status), lease, clock(), result.reason_code
            )
            raise ValueError(result.reason_code)
        candidate = result.candidate
        observation = parse_code_list(candidate.body, received[0].date())

        def validate_body(body: bytes) -> None:
            parse_code_list(body, received[0].date())

        reference = RawArtifactPublisher(runs, repo).publish(
            candidate,
            RawPublicationIntent(
                publication_id=f"edinet-code-list-publication-{uid}",
                task_id=task_id,
                logical_request_id=logical.logical_request_id,
                physical_attempt_id=attempt.physical_attempt_id,
                source_id="edinet-code-list",
                operation="code-list-zip",
                content_sha256=candidate.sha256,
                byte_count=len(candidate.body),
                media_type=candidate.media_type,
                encoding=candidate.encoding,
                raw_schema_id="edinet-code-list-zip-raw",
                raw_schema_version=1,
                publication_generation=lease.generation,
            ),
            lease,
            clock(),
            clock(),
            validate_body,
        )
        receipt = CodeListReceipt(
            requested_at=now.isoformat(),
            received_at=received[0].isoformat(),
            recorded_at=clock().isoformat(),
            url=CODE_LIST_URL,
            observation=observation,
            raw_reference=reference,
        )
        files = {
            "approval.yaml": (config / "source-approvals/edinet-code-list/v1.yaml").read_bytes(),
            "profile.yaml": (config / "source-profiles/edinet-code-list/v1.yaml").read_bytes(),
            "receipt.json": receipt.model_dump_json(indent=2).encode(),
        }
        files["index.json"] = json.dumps(
            {name: sha256(body).hexdigest() for name, body in sorted(files.items())}, indent=2
        ).encode()

        def validate_saved(saved: Mapping[str, bytes]) -> None:
            validate_code_list_receipt(saved, runtime=runtime, runs=runs)

        publish_preparation(
            runs,
            destination,
            files,
            validator=validate_saved,
        )
        coordinator.finalize_logical_request(
            logical.logical_request_id, LogicalResultOutcome.SUCCEEDED, lease, clock(), None
        )
        return receipt
    finally:
        repo.release_lease(lease, clock())
