"""Single BOJ exchange, validated raw publication and immutable offline evidence."""

import json
import os
import re
import stat
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime
from hashlib import sha256
from pathlib import Path
from typing import Literal
from uuid import uuid4

import httpx

from stock_research_llm_orchestrator.contracts.base import StrictContractModel
from stock_research_llm_orchestrator.preparation.market_revalidation import _safe_path
from stock_research_llm_orchestrator.preparation.storage import publish_preparation
from stock_research_llm_orchestrator.requests.production import (
    CommittedRawReference,
    LogicalResultOutcome,
    ProductionCachePolicy,
    ProductionLogicalRequest,
    ProductionLogicalResult,
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
    ReceivedResponseValidationError,
    TemporaryRawCandidate,
    TransportValidationPolicy,
    UntrustedTransportResponse,
)
from stock_research_llm_orchestrator.sources.boj.code_api import BojFxCodeAdapter, BojFxDailySeries
from stock_research_llm_orchestrator.sources.boj.httpx_transport import BojHttpClientPolicy, BojPhysicalTransport
from stock_research_llm_orchestrator.sources.boj.production_policy import (
    APPROVAL_SHA256,
    PROFILE_SHA256,
    fingerprint,
    fx_intent,
    gate_keys,
    gate_policy,
    load_boj_binding,
)
from stock_research_llm_orchestrator.sources.protocol import BoundedSourceResponse, CredentialFreeSourceIntent


class FxMetadata(StrictContractModel):
    """Received time is distinct from provider generation and observation times."""

    version: Literal[1] = 1
    validation_mode: Literal["online", "offline_revalidation"] = "online"
    validated_at: str
    period_start: str
    period_end: str
    received_at: str
    intent: CredentialFreeSourceIntent
    raw_reference: CommittedRawReference
    observation_basis: Literal["BOJ USDJPY bid-ask midpoint at 17:00 Asia/Tokyo"] = (
        "BOJ USDJPY bid-ask midpoint at 17:00 Asia/Tokyo"
    )
    publication_time: None = None
    semantics_reference: Literal["https://www.boj.or.jp/en/statistics/market/forex/fxdaily/"] = (
        "https://www.boj.or.jp/en/statistics/market/forex/fxdaily/"
    )


class FxIndex(StrictContractModel):
    """Locally verifiable evidence; downstream completeness is a separate decision."""

    version: Literal[1] = 1
    analysis_ready: Literal[False] = False
    status: Literal["accepted", "accepted_with_limitations"]
    files: dict[str, str]
    invalid_dates: tuple[str, ...]
    null_dates: tuple[str, ...]


def read_bundle(directory: Path) -> dict[str, bytes]:
    """Read nested exact bytes without following symlinks."""
    root = _safe_path(directory)
    if not root.is_dir():
        raise ValueError("bundle_directory_required")
    result = {}
    for path in sorted(root.rglob("*")):
        safe = _safe_path(path)
        if safe.is_file():
            result[safe.relative_to(root).as_posix()] = safe.read_bytes()
        elif not safe.is_dir():
            raise ValueError("invalid_bundle_entry")
    return result


def _series(body: bytes, metadata: FxMetadata) -> BojFxDailySeries:
    ref = metadata.raw_reference
    if ref.raw_schema_id != "boj-fx-daily-code-raw" or ref.raw_schema_version != 1:
        raise ValueError("fx_raw_schema_mismatch")
    if len(body) != ref.byte_count or sha256(body).hexdigest() != ref.content_sha256:
        raise ValueError("fx_raw_hash_mismatch")
    return BojFxCodeAdapter().parse_for_intent(
        BoundedSourceResponse(
            physical_attempt_id=ref.physical_attempt_id,
            body=body,
            sha256=ref.content_sha256,
            media_type="application/json",
            encoding="utf-8",
        ),
        metadata.intent,
    )


def _index(files: Mapping[str, bytes]) -> FxIndex:
    metadata = FxMetadata.model_validate_json(files["metadata.json"])
    received = datetime.fromisoformat(metadata.received_at)
    if received.tzinfo is None:
        raise ValueError("fx_received_time_requires_timezone")
    validated = datetime.fromisoformat(metadata.validated_at)
    if validated.tzinfo is None or validated < received:
        raise ValueError("fx_validation_time_invalid")
    start, end = date.fromisoformat(metadata.period_start), date.fromisoformat(metadata.period_end)
    if metadata.intent != fx_intent(start, end):
        raise ValueError("fx_intent_period_mismatch")
    if (
        sha256(files["approval.yaml"]).hexdigest() != APPROVAL_SHA256
        or sha256(files["profile.yaml"]).hexdigest() != PROFILE_SHA256
    ):
        raise ValueError("fx_configuration_mismatch")
    # Exact approved bytes establish the validity interval even during offline replay.
    if not date(2026, 9, 26) <= received.date() < date(2026, 12, 26):
        raise ValueError("fx_acquisition_outside_approval")
    series = _series(files["body.bin"], metadata)
    if BojFxDailySeries.model_validate_json(files["normalized.json"]) != series:
        raise ValueError("fx_normalization_mismatch")
    expected = {"metadata.json", "body.bin", "normalized.json", "approval.yaml", "profile.yaml"}
    if metadata.validation_mode == "offline_revalidation":
        original = ProductionLogicalResult.model_validate_json(files["acquisition-result.json"])
        if (
            original.outcome is not LogicalResultOutcome.FAILED
            or original.error_code != "boj_source_validation_failed"
            or original.logical_request_id != metadata.raw_reference.logical_request_id
            or not received <= datetime.fromisoformat(original.completed_at) <= validated
        ):
            raise ValueError("fx_original_failure_mismatch")
        expected.add("acquisition-result.json")
    if set(files) - {"index.json"} != expected:
        raise ValueError("fx_file_inventory_mismatch")
    invalid = tuple(
        row.observed_on
        for row in series.observations
        if row.value is not None and (not row.value.is_finite() or row.value <= 0)
    )
    nulls = tuple(row.observed_on for row in series.observations if row.value is None)
    return FxIndex(
        status="accepted_with_limitations" if invalid or nulls or not series.observations else "accepted",
        files={name: sha256(files[name]).hexdigest() for name in sorted(expected)},
        invalid_dates=invalid,
        null_dates=nulls,
    )


def validate_fx_evidence(files: Mapping[str, bytes]) -> None:
    """Reparse exact raw and reproduce all retained normalization and hashes."""
    if json.loads(files["index.json"]).get("version") == 2:
        from stock_research_llm_orchestrator.preparation.dukascopy_fx import validate_dukascopy_fx

        validate_dukascopy_fx(files)
        return
    if FxIndex.model_validate_json(files["index.json"]) != _index(files):
        raise ValueError("fx_index_mismatch")


def acquire_fx_evidence(
    *,
    config: Path,
    runtime: Path,
    runs: Path,
    destination: str,
    task_id: str,
    start: date,
    end: date,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    transport: httpx.BaseTransport | None = None,
) -> FxIndex:
    """Perform one coordinated GET; never retry a failed or incomplete exchange."""
    config, runtime, runs = _safe_path(config), _safe_path(runtime), _safe_path(runs)
    output = _safe_path(runs / destination)
    if (
        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", destination)
        or output.parent != runs
        or destination == task_id
        or output.exists()
    ):
        raise ValueError("fx_destination_invalid_or_exists")
    binding = load_boj_binding(config, clock().date())
    intent = fx_intent(start, end)
    configuration = {
        "approval.yaml": (config / "source-approvals/boj/v2.yaml").read_bytes(),
        "profile.yaml": (config / "source-profiles/boj/v2.yaml").read_bytes(),
    }
    runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
    runs.mkdir(mode=0o700, parents=True, exist_ok=True)
    if runs.stat().st_uid != os.geteuid() or stat.S_IMODE(runs.stat().st_mode) != 0o700:
        raise ValueError("fx_runs_root_requires_private_permissions")
    repository = initialize_runtime_storage(runtime)
    uid = uuid4().hex
    logical = ProductionLogicalRequest(
        logical_request_id=f"boj-{uid}",
        task_id=task_id,
        source_id="boj",
        operation="fx-daily-code",
        request_fingerprint=fingerprint(intent),
        source_approval_version=2,
        source_profile_version=2,
        credential_scope_alias=None,
        egress_scope="default-egress",
        created_at=clock().isoformat(),
    )
    lease = repository.acquire_lease(
        f"boj-owner-{uid}", clock(), RuntimeLeasePolicy(lease_duration_seconds=120, heartbeat_interval_seconds=30)
    )
    received: list[datetime] = []
    physical = BojPhysicalTransport(
        intent,
        BojHttpClientPolicy(
            connect_timeout_seconds=10, read_timeout_seconds=30, write_timeout_seconds=10, pool_timeout_seconds=10
        ),
        transport,
        clock,
        received.append,
    )
    coordinator = ProductionTransportCoordinator(repository, physical, lambda: f"permit-{uid}")
    release_allowed = True
    try:
        repository.admit_logical_request(
            logical, binding.profile.rate_domain, ProductionCachePolicy(applicable=False), lease, clock(), QueuePolicy()
        )
        claimed = repository.claim_next_queued(binding.profile.rate_domain, lease, clock())
        if claimed is None or claimed.logical_request_id != logical.logical_request_id:
            raise ValueError("boj_request_not_claimed")
        attempt = ProductionPhysicalAttempt(
            physical_attempt_id=f"boj-attempt-{uid}",
            logical_request_id=logical.logical_request_id,
            sequence_number=1,
            lease_generation=lease.generation,
            created_at=clock().isoformat(),
        )

        def send(request: PhysicalTransportRequest, _envelope: object) -> tuple[UntrustedTransportResponse, object]:
            try:
                load_boj_binding(config, clock().date())
            except ValueError:
                raise ReceivedResponseValidationError("boj_binding_changed_before_send") from None
            response = physical(request)
            return response, None

        try:
            exchange = coordinator.execute_exchange_with_callback(
                intent.to_transport_request(logical.logical_request_id, attempt.physical_attempt_id),
                attempt,
                f"reservation-{uid}",
                gate_keys(task_id),
                gate_policy(),
                TransportValidationPolicy(allowed_media_types=("application/json",), allowed_encodings=("utf-8",)),
                lease,
                clock(),
                clock,
                None,
                send,
            )
        except RuntimeError as error:
            if str(error).startswith("gate_blocked:"):
                coordinator.finalize_logical_request(
                    logical.logical_request_id, LogicalResultOutcome.FAILED, lease, clock(), "boj_gate_blocked"
                )
            else:
                release_allowed = False
            raise
        result = exchange.execution
        if result.status != "succeeded" or result.candidate is None:
            coordinator.finalize_logical_request(
                logical.logical_request_id, LogicalResultOutcome(result.status), lease, clock(), result.reason_code
            )
            raise ValueError(result.reason_code)
        candidate = result.candidate
        reference_intent = RawPublicationIntent(
            publication_id=f"publication-{uid}",
            task_id=task_id,
            logical_request_id=logical.logical_request_id,
            physical_attempt_id=attempt.physical_attempt_id,
            source_id="boj",
            operation="fx-daily-code",
            content_sha256=candidate.sha256,
            byte_count=len(candidate.body),
            media_type=candidate.media_type,
            encoding=candidate.encoding,
            raw_schema_id="boj-fx-daily-code-raw",
            raw_schema_version=1,
            publication_generation=lease.generation,
        )

        def validate(body: bytes) -> None:
            BojFxCodeAdapter().parse_for_intent(
                BoundedSourceResponse(
                    physical_attempt_id=attempt.physical_attempt_id,
                    body=body,
                    sha256=candidate.sha256,
                    media_type=candidate.media_type,
                    encoding=candidate.encoding,
                ),
                intent,
            )

        try:
            validate(candidate.body)
        except ValueError:
            coordinator.finalize_logical_request(
                logical.logical_request_id, LogicalResultOutcome.FAILED, lease, clock(), "boj_source_validation_failed"
            )
            publish_preparation(
                runs,
                destination,
                {
                    "unaccepted-body.bin": candidate.body,
                    "failure.txt": b"boj_source_validation_failed; unaccepted diagnostic only; no automatic resend\n",
                    "request.json": intent.model_dump_json(indent=2).encode(),
                    "diagnostic.json": json.dumps(
                        {
                            "status": "failed",
                            "analysis_ready": False,
                            "received_at": received[0].isoformat(),
                            "raw_sha256": candidate.sha256,
                            "physical_attempt_id": candidate.physical_attempt_id,
                            "approval_sha256": APPROVAL_SHA256,
                            "profile_sha256": PROFILE_SHA256,
                            "reason": "boj_source_validation_failed",
                        },
                        indent=2,
                    ).encode(),
                },
                validator=lambda _files: None,
            )
            raise ValueError("boj_source_validation_failed") from None
        release_allowed = False
        # Publication exceptions leave the logical request unfinished for fenced recovery.
        reference = RawArtifactPublisher(runs, repository).publish(
            candidate, reference_intent, lease, clock(), clock(), validate
        )
        metadata = FxMetadata(
            validated_at=clock().isoformat(),
            period_start=start.isoformat(),
            period_end=end.isoformat(),
            received_at=received[0].isoformat(),
            intent=intent,
            raw_reference=reference,
        )
        files = {
            **configuration,
            "body.bin": candidate.body,
            "metadata.json": metadata.model_dump_json(indent=2).encode(),
            "normalized.json": _series(candidate.body, metadata).model_dump_json(indent=2).encode(),
        }
        index = _index(files)
        files["index.json"] = index.model_dump_json(indent=2).encode()
        publish_preparation(runs, destination, files, validator=validate_fx_evidence)
        coordinator.finalize_logical_request(
            logical.logical_request_id, LogicalResultOutcome.SUCCEEDED, lease, clock(), None
        )
        release_allowed = True
        return index
    finally:
        now = clock()
        if release_allowed and now < datetime.fromisoformat(lease.expires_at):
            repository.release_lease(lease, now)


def revalidate_fx_diagnostic(
    *,
    config: Path,
    runtime: Path,
    runs: Path,
    diagnostic: Path,
    destination: str,
    task_id: str,
    logical_request_id: str,
    start: date,
    end: date,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> FxIndex:
    """Accept saved bytes without sending or rewriting the original failed logical result."""
    config, runtime, runs, diagnostic = map(_safe_path, (config, runtime, runs, diagnostic))
    output = _safe_path(runs / destination)
    if (
        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", destination)
        or output.parent != runs
        or output.exists()
        or destination == task_id
        or output == diagnostic
        or output in diagnostic.parents
        or diagnostic in output.parents
    ):
        raise ValueError("fx_destination_invalid_or_exists")
    load_boj_binding(config, clock().date())
    saved = read_bundle(diagnostic)
    record = FxDiagnostic.model_validate_json(saved["diagnostic.json"])
    intent = CredentialFreeSourceIntent.model_validate_json(saved["request.json"])
    if (
        intent != fx_intent(start, end)
        or record.approval_sha256 != APPROVAL_SHA256
        or record.profile_sha256 != PROFILE_SHA256
    ):
        raise ValueError("fx_diagnostic_binding_mismatch")
    body = saved["unaccepted-body.bin"]
    response = BoundedSourceResponse(
        physical_attempt_id=record.physical_attempt_id,
        body=body,
        sha256=record.raw_sha256,
        media_type="application/json",
        encoding="utf-8",
    )
    series = BojFxCodeAdapter().parse_for_intent(response, intent)
    repository = initialize_runtime_storage(runtime)
    original = repository.logical_result(logical_request_id)
    state = repository.physical_attempt_state(record.physical_attempt_id)
    if (
        original is None
        or original.outcome is not LogicalResultOutcome.FAILED
        or original.error_code != "boj_source_validation_failed"
        or state is None
        or state[0] != "succeeded"
    ):
        raise ValueError("fx_diagnostic_runtime_mismatch")
    candidate = TemporaryRawCandidate(
        physical_attempt_id=record.physical_attempt_id,
        body=body,
        sha256=record.raw_sha256,
        media_type="application/json",
        encoding="utf-8",
    )
    uid = uuid4().hex
    lease = repository.acquire_lease(f"boj-offline-{uid}", clock(), RuntimeLeasePolicy())
    completed = False
    try:
        publication = RawPublicationIntent(
            publication_id=f"publication-{uid}",
            task_id=task_id,
            logical_request_id=logical_request_id,
            physical_attempt_id=record.physical_attempt_id,
            source_id="boj",
            operation="fx-daily-code",
            content_sha256=record.raw_sha256,
            byte_count=len(body),
            media_type="application/json",
            encoding="utf-8",
            raw_schema_id="boj-fx-daily-code-raw",
            raw_schema_version=1,
            publication_generation=lease.generation,
        )

        def validate(raw: bytes) -> None:
            if raw != body:
                raise ValueError("fx_diagnostic_bytes_changed")
            BojFxCodeAdapter().parse_for_intent(response, intent)

        reference = RawArtifactPublisher(runs, repository).publish(
            candidate, publication, lease, clock(), clock(), validate
        )
        metadata = FxMetadata(
            validation_mode="offline_revalidation",
            validated_at=clock().isoformat(),
            period_start=start.isoformat(),
            period_end=end.isoformat(),
            received_at=record.received_at,
            intent=intent,
            raw_reference=reference,
        )
        files = {
            "body.bin": body,
            "metadata.json": metadata.model_dump_json(indent=2).encode(),
            "normalized.json": series.model_dump_json(indent=2).encode(),
            "acquisition-result.json": original.model_dump_json(indent=2).encode(),
            "approval.yaml": (config / "source-approvals/boj/v2.yaml").read_bytes(),
            "profile.yaml": (config / "source-profiles/boj/v2.yaml").read_bytes(),
        }
        index = _index(files)
        files["index.json"] = index.model_dump_json(indent=2).encode()
        publish_preparation(runs, destination, files, validator=validate_fx_evidence)
        completed = True
        return index
    finally:
        if completed:
            repository.release_lease(lease, clock())


class FxDiagnostic(StrictContractModel):
    """Hash-bound receipt of a source-validation failure, never an acceptance claim."""

    status: Literal["failed"]
    analysis_ready: Literal[False]
    received_at: str
    raw_sha256: str
    physical_attempt_id: str
    approval_sha256: str
    profile_sha256: str
    reason: Literal["boj_source_validation_failed"]
