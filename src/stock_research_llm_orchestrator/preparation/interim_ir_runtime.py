"""One-shot coordinated IR acquisition and live database provenance checks."""

import json
import os
import stat
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from hashlib import sha256
from pathlib import Path
from typing import Literal
from uuid import uuid4

import httpx

from stock_research_llm_orchestrator.contracts.base import StrictContractModel, Timestamp
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.task import DetailedAnalysisTaskV1
from stock_research_llm_orchestrator.preparation.edinet_campaign import _durable_write
from stock_research_llm_orchestrator.preparation.fx_evidence import read_bundle
from stock_research_llm_orchestrator.preparation.market_revalidation import _safe_path, _timestamp
from stock_research_llm_orchestrator.preparation.price_fx_run import publish_run_preparation
from stock_research_llm_orchestrator.requests.models import SourceBinding
from stock_research_llm_orchestrator.requests.production import (
    CommittedRawReference,
    LogicalResultOutcome,
    ProductionCachePolicy,
    ProductionLogicalRequest,
    ProductionPhysicalAttempt,
    QueuePolicy,
    RawPublicationIntent,
    RuntimeLeasePolicy,
)
from stock_research_llm_orchestrator.requests.raw_artifacts import RawArtifactPublisher
from stock_research_llm_orchestrator.requests.storage import ProductionRequestRepository, initialize_runtime_storage
from stock_research_llm_orchestrator.requests.transport import (
    PhysicalTransportRequest,
    ProductionTransportCoordinator,
    TransportValidationPolicy,
    UntrustedTransportResponse,
)
from stock_research_llm_orchestrator.sources.protocol import CredentialFreeSourceIntent
from stock_research_llm_orchestrator.sources.toyota_ir import disclosure_policy
from stock_research_llm_orchestrator.sources.toyota_ir.httpx_transport import ToyotaIrTransport
from stock_research_llm_orchestrator.sources.toyota_ir.production_policy import (
    APPROVAL_SHA256,
    PROFILE_SHA256,
    fingerprint,
    gates,
    intent_for,
    keys,
    load_binding,
    load_policy,
)
from stock_research_llm_orchestrator.sources.toyota_ir_pdf import extract_pages


CAMPAIGN = "toyota-ir-eight-pdf-20260928"
LEASE = RuntimeLeasePolicy(lease_duration_seconds=120, heartbeat_interval_seconds=30)


class IrRuntimeReceipt(StrictContractModel):
    """Receipt time and committed identity of one fixed PDF."""

    key: str
    received_at: Timestamp
    raw_reference: CommittedRawReference


class IrRuntimeManifest(StrictContractModel):
    """Runtime acquisition supplement, never a replacement for operator adoption."""

    version: Literal[1] = 1
    kind: Literal["toyota-ir-runtime-acquisition", "toyota-disclosure-runtime-candidates"] = (
        "toyota-ir-runtime-acquisition"
    )
    analysis_ready: Literal[False] = False
    task_id: str
    receipts: tuple[IrRuntimeReceipt, ...]
    hashes: dict[str, str]


@dataclass(frozen=True)
class RuntimeTarget:
    """A fixed download location with an optional previously adopted hash."""

    key: str
    published_on: date
    sha256: str | None


@dataclass(frozen=True)
class RuntimeSpec:
    """The two concrete IR campaigns share publication and persistence mechanics."""

    campaign: str
    kind: Literal["toyota-ir-runtime-acquisition", "toyota-disclosure-runtime-candidates"]
    version: int
    operation: str
    approval_sha256: str
    profile_sha256: str
    targets: tuple[RuntimeTarget, ...]
    marker_targets: list[dict[str, object]]
    intent: Callable[[str], CredentialFreeSourceIntent]
    fingerprint: Callable[[CredentialFreeSourceIntent, str], str]
    binding: Callable[[Path, date], SourceBinding]


def _spec(policy_bytes: bytes, latest_disclosures: bool) -> RuntimeSpec:
    if latest_disclosures:
        policy = disclosure_policy.load_policy(policy_bytes)
        return RuntimeSpec(
            disclosure_policy.CAMPAIGN,
            disclosure_policy.KIND,
            2,
            disclosure_policy.OPERATION,
            disclosure_policy.APPROVAL_SHA256,
            disclosure_policy.PROFILE_SHA256,
            tuple(RuntimeTarget(d.key, d.published_on, None) for d in policy.documents),
            [d.model_dump(mode="json") for d in policy.documents],
            lambda key: disclosure_policy.intent_for(policy, key),
            disclosure_policy.fingerprint,
            disclosure_policy.load_binding,
        )
    interim = load_policy(policy_bytes)
    return RuntimeSpec(
        CAMPAIGN,
        "toyota-ir-runtime-acquisition",
        1,
        "interim-pdf",
        APPROVAL_SHA256,
        PROFILE_SHA256,
        tuple(RuntimeTarget(d.key, d.published_on, d.sha256) for d in interim.documents),
        [d.model_dump(mode="json") for d in interim.documents],
        lambda key: intent_for(interim, key),
        fingerprint,
        load_binding,
    )


def _validate_body(body: bytes, expected_hash: str | None) -> None:
    if expected_hash is not None:
        if sha256(body).hexdigest() != expected_hash:
            raise ValueError("ir_runtime_content_changed")
    elif not extract_pages(body):
        raise ValueError("disclosure_pdf_unreadable")


def verify_runtime(
    files: Mapping[str, bytes],
    repository: ProductionRequestRepository,
    raw_root: Path,
    task: DetailedAnalysisTaskV1,
    runtime: Path,
) -> IrRuntimeManifest:
    """Require canonical DB success, campaign slots and every published raw byte."""
    manifest = IrRuntimeManifest.model_validate_json(files["manifest.json"])
    spec = _spec(files["policy.json"], manifest.kind == disclosure_policy.KIND)
    if set(files) != {"manifest.json", "task.json", "policy.json", "approval.yaml", "profile.yaml"}:
        raise ValueError("ir_runtime_inventory_invalid")
    if (
        manifest.task_id != task.task_id
        or DetailedAnalysisTaskV1.model_validate_json(files["task.json"]) != task
        or task.security.security_code != "7203"
        or manifest.hashes != {n: sha256(b).hexdigest() for n, b in files.items() if n != "manifest.json"}
        or sha256(files["approval.yaml"]).hexdigest() != spec.approval_sha256
        or sha256(files["profile.yaml"]).hexdigest() != spec.profile_sha256
        or tuple(r.key for r in manifest.receipts) != tuple(d.key for d in spec.targets)
        or len({r.raw_reference.publication_id for r in manifest.receipts}) != len(spec.targets)
    ):
        raise ValueError("ir_runtime_binding_invalid")
    raw_root = _safe_path(raw_root)
    marker = _safe_path(runtime / spec.campaign)
    if {p.name for p in marker.iterdir()} != {"started.json"} | {f"slot-{i}.json" for i in range(len(spec.targets))}:
        raise ValueError("ir_runtime_campaign_incomplete")
    started = json.loads(_safe_path(marker / "started.json").read_bytes())
    if (
        started["task_id"] != task.task_id
        or started["max_sends"] != len(spec.targets)
        or started["approval_sha256"] != spec.approval_sha256
        or started["profile_sha256"] != spec.profile_sha256
        or started["policy_sha256"] != sha256(files["policy.json"]).hexdigest()
        or started["targets"] != spec.marker_targets
    ):
        raise ValueError("ir_runtime_campaign_binding_invalid")
    for number, (document, receipt) in enumerate(zip(spec.targets, manifest.receipts, strict=True)):
        ref = receipt.raw_reference
        request = repository.logical_request(ref.logical_request_id)
        record = repository.raw_publication_record(ref.publication_id)
        result = repository.logical_result(ref.logical_request_id)
        intent = spec.intent(receipt.key)
        slot = json.loads(_safe_path(marker / f"slot-{number}.json").read_bytes())
        if (
            slot["key"] != receipt.key
            or slot["logical_request_id"] != ref.logical_request_id
            or slot["physical_attempt_id"] != ref.physical_attempt_id
            or slot["fingerprint"] != spec.fingerprint(intent, task.task_id)
            or not _timestamp(started["started_at"])
            <= _timestamp(slot["started_at"])
            <= _timestamp(receipt.received_at)
        ):
            raise ValueError("ir_runtime_slot_binding_invalid")
        if (
            request is None
            or record is None
            or result is None
            or request.task_id != task.task_id
            or request.source_id != "toyota-ir"
            or request.operation != spec.operation
            or request.request_fingerprint != spec.fingerprint(intent, task.task_id)
            or request.source_approval_version != spec.version
            or request.source_profile_version != spec.version
            or request.state.value != "succeeded"
            or request.credential_scope_alias is not None
            or request.egress_scope != "default-egress"
            or ref.task_id != task.task_id
            or (document.sha256 is not None and ref.content_sha256 != document.sha256)
            or ref.raw_schema_id != "toyota-ir-pdf-raw"
            or ref.raw_schema_version != 1
            or record.state != "committed"
            or record.relative_path != ref.relative_path
            or record.intent.source_id != "toyota-ir"
            or record.intent.operation != spec.operation
            or ref not in repository.committed_raw_references(ref.logical_request_id)
            or repository.physical_attempt_identity(ref.physical_attempt_id)
            != (ref.logical_request_id, 1, ref.publication_generation)
            or repository.physical_attempt_state(ref.physical_attempt_id) != ("succeeded", ref.publication_generation)
            or result.outcome != LogicalResultOutcome.SUCCEEDED
            or not _timestamp(request.created_at) <= _timestamp(receipt.received_at) <= _timestamp(result.completed_at)
            or _timestamp(receipt.received_at).date() < document.published_on
        ):
            raise ValueError("ir_runtime_database_binding_invalid")
        raw = read_bundle(raw_root / ref.task_id / ref.relative_path)
        if (
            set(raw) != {"body.bin", "request.json", "response.json", "receipt.json"}
            or sha256(raw["body.bin"]).hexdigest() != ref.content_sha256
            or len(raw["body.bin"]) != ref.byte_count
            or json.loads(raw["receipt.json"]) != {"body_sha256": ref.content_sha256, "byte_count": ref.byte_count}
            or json.loads(raw["request.json"])
            != {
                k: getattr(record.intent, k)
                for k in (
                    "logical_request_id",
                    "operation",
                    "physical_attempt_id",
                    "publication_generation",
                    "publication_id",
                    "source_id",
                    "task_id",
                )
            }
            or json.loads(raw["response.json"])
            != {
                k: getattr(record.intent, k)
                for k in ("byte_count", "encoding", "media_type", "raw_schema_id", "raw_schema_version")
            }
        ):
            raise ValueError("ir_runtime_published_raw_mismatch")
        _validate_body(raw["body.bin"], document.sha256)
    return manifest


def acquire_runtime(
    *,
    config: Path,
    runtime: Path,
    raw_root: Path,
    output: Path,
    task_bytes: bytes,
    policy_bytes: bytes,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    sleep: Callable[[float], None] = time.sleep,
    transport: httpx.BaseTransport | None = None,
    latest_disclosures: bool = False,
) -> IrRuntimeManifest:
    """Consume the fixed campaign at creation; never resume or retry."""
    config, runtime, raw_root, output = map(_safe_path, (config, runtime, raw_root, output))
    if output.parent != raw_root or output.exists():
        raise ValueError("ir_runtime_output_invalid")
    task = DetailedAnalysisTaskV1.model_validate_json(task_bytes)
    spec = _spec(policy_bytes, latest_disclosures)
    if task.security.security_code != "7203":
        raise ValueError("ir_runtime_task_scope_invalid")
    spec.binding(config, clock().date())
    raw_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if raw_root.stat().st_uid != os.geteuid() or stat.S_IMODE(raw_root.stat().st_mode) != 0o700:
        raise ValueError("ir_runtime_private_root_required")
    runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
    repository = initialize_runtime_storage(runtime)
    lease = repository.acquire_lease(f"ir-marker-{uuid4().hex}", clock(), LEASE)
    try:
        marker = _safe_path(runtime / spec.campaign)
        marker.mkdir(mode=0o700)
        descriptor = os.open(runtime, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        _durable_write(
            marker / "started.json",
            {
                "task_id": task.task_id,
                "max_sends": len(spec.targets),
                "approval_sha256": spec.approval_sha256,
                "profile_sha256": spec.profile_sha256,
                "policy_sha256": sha256(policy_bytes).hexdigest(),
                "started_at": clock().isoformat(),
                "targets": spec.marker_targets,
            },
        )
    finally:
        repository.release_lease(lease, clock())
    receipts = []
    for number, document in enumerate(spec.targets):
        if number:
            sleep(30)
            sleep(30)
        intent = spec.intent(document.key)
        lease = repository.acquire_lease(f"ir-owner-{uuid4().hex}", clock(), LEASE)
        release_allowed = True
        uid = uuid4().hex
        logical = ProductionLogicalRequest(
            logical_request_id=f"ir-{uid}",
            task_id=task.task_id,
            source_id="toyota-ir",
            operation=spec.operation,
            request_fingerprint=spec.fingerprint(intent, task.task_id),
            source_approval_version=spec.version,
            source_profile_version=spec.version,
            credential_scope_alias=None,
            egress_scope="default-egress",
            created_at=clock().isoformat(),
        )
        try:
            binding = spec.binding(config, clock().date())
            repository.admit_logical_request(
                logical,
                binding.profile.rate_domain,
                ProductionCachePolicy(applicable=False),
                lease,
                clock(),
                QueuePolicy(),
            )
            claim = repository.claim_next_queued(binding.profile.rate_domain, lease, clock())
            if claim is None or claim.logical_request_id != logical.logical_request_id:
                raise ValueError("ir_runtime_queue_not_claimed")
            lease = repository.heartbeat_lease(lease, clock(), LEASE)
            received: list[datetime] = []
            physical = ToyotaIrTransport(intent, policy_bytes, clock, received.append, transport, latest_disclosures)
            coordinator = ProductionTransportCoordinator(repository, physical, lambda: f"permit-{uuid4().hex}")
            attempt = ProductionPhysicalAttempt(
                physical_attempt_id=f"ir-attempt-{uid}",
                logical_request_id=logical.logical_request_id,
                sequence_number=1,
                lease_generation=lease.generation,
                created_at=clock().isoformat(),
            )

            def send(
                request: PhysicalTransportRequest,
                _envelope: object,
                number: int = number,
                key: str = document.key,
                logical: ProductionLogicalRequest = logical,
                attempt: ProductionPhysicalAttempt = attempt,
                physical: ToyotaIrTransport = physical,
            ) -> tuple[UntrustedTransportResponse, object]:
                spec.binding(config, clock().date())
                _durable_write(
                    marker / f"slot-{number}.json",
                    {
                        "key": key,
                        "logical_request_id": logical.logical_request_id,
                        "physical_attempt_id": attempt.physical_attempt_id,
                        "fingerprint": logical.request_fingerprint,
                        "started_at": clock().isoformat(),
                    },
                )
                return physical(request), None

            release_allowed = False
            exchange = coordinator.execute_exchange_with_callback(
                intent.to_transport_request(logical.logical_request_id, attempt.physical_attempt_id),
                attempt,
                f"reservation-{uid}",
                keys(task.task_id).model_copy(update={"operation": spec.operation}),
                gates(),
                TransportValidationPolicy(allowed_media_types=("application/pdf",), allowed_encodings=("binary",)),
                lease,
                clock(),
                clock,
                None,
                send,
            )
            lease = repository.heartbeat_lease(lease, clock(), LEASE)
            result = exchange.execution
            if result.status != "succeeded" or result.candidate is None:
                coordinator.finalize_logical_request(
                    logical.logical_request_id, LogicalResultOutcome(result.status), lease, clock(), result.reason_code
                )
                release_allowed = True
                raise ValueError("ir_runtime_exchange_failed")
            candidate = result.candidate

            def validate(body: bytes, expected_hash: str | None = document.sha256) -> None:
                _validate_body(body, expected_hash)

            try:
                validate(candidate.body)
            except ValueError:
                _durable_write(
                    marker / f"failure-{number}.json",
                    {"reason": "content_changed", "key": document.key, "sha256": candidate.sha256},
                )
                publish_run_preparation(
                    raw_root, f"unaccepted-{uid}", {"body.bin": candidate.body}, validator=lambda saved: None
                )
                coordinator.finalize_logical_request(
                    logical.logical_request_id, LogicalResultOutcome.FAILED, lease, clock(), "ir_content_changed"
                )
                release_allowed = True
                raise
            reference = RawArtifactPublisher(raw_root, repository).publish(
                candidate,
                RawPublicationIntent(
                    publication_id=f"ir-publication-{uid}",
                    task_id=task.task_id,
                    logical_request_id=logical.logical_request_id,
                    physical_attempt_id=attempt.physical_attempt_id,
                    source_id="toyota-ir",
                    operation=spec.operation,
                    content_sha256=candidate.sha256,
                    byte_count=len(candidate.body),
                    media_type=candidate.media_type,
                    encoding=candidate.encoding,
                    raw_schema_id="toyota-ir-pdf-raw",
                    raw_schema_version=1,
                    publication_generation=lease.generation,
                ),
                lease,
                clock(),
                clock(),
                validate,
            )
            coordinator.finalize_logical_request(
                logical.logical_request_id, LogicalResultOutcome.SUCCEEDED, lease, clock(), None
            )
            release_allowed = True
            receipts.append(
                IrRuntimeReceipt(key=document.key, received_at=received[0].isoformat(), raw_reference=reference)
            )
        finally:
            if release_allowed and clock() < _timestamp(lease.expires_at):
                repository.release_lease(lease, clock())
    files = {
        "task.json": task_bytes,
        "policy.json": policy_bytes,
        "approval.yaml": (config / f"source-approvals/toyota-ir/v{spec.version}.yaml").read_bytes(),
        "profile.yaml": (config / f"source-profiles/toyota-ir/v{spec.version}.yaml").read_bytes(),
    }
    manifest = IrRuntimeManifest(
        kind=spec.kind,
        task_id=task.task_id,
        receipts=tuple(receipts),
        hashes={n: sha256(b).hexdigest() for n, b in files.items()},
    )
    files["manifest.json"] = manifest.model_dump_json(indent=2).encode()
    verify_runtime(files, repository, raw_root, task, runtime)

    def validate_saved(saved: Mapping[str, bytes]) -> None:
        verify_runtime(saved, repository, raw_root, task, runtime)

    publish_run_preparation(raw_root, output.name, files, validator=validate_saved)
    return manifest
