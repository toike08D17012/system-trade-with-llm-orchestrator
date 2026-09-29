"""Coordinated annual FX acquisition and immutable version-two evidence."""

import json
import os
import re
import stat
import time
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Literal
from uuid import uuid4

import httpx

from stock_research_llm_orchestrator.contracts.base import StrictContractModel
from stock_research_llm_orchestrator.preparation.market_revalidation import _read_file, _safe_path, _timestamp
from stock_research_llm_orchestrator.preparation.storage import publish_preparation
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
from stock_research_llm_orchestrator.sources.dukascopy.daily import (
    DailyRow,
    DailySeries,
    bridge,
    intent_for_url,
    parse_daily,
    urls,
)
from stock_research_llm_orchestrator.sources.dukascopy.httpx_transport import (
    DukascopyHttpClientPolicy,
    DukascopyPhysicalTransport,
)
from stock_research_llm_orchestrator.sources.dukascopy.production_policy import (
    APPROVAL_SHA256,
    PROFILE_SHA256,
    gate_keys,
    gate_policy,
    load_dukascopy_binding,
)


class YearReceipt(StrictContractModel):
    """Exact year target, receipt time and committed raw identity."""

    year: int
    url: str
    received_at: str
    raw_reference: CommittedRawReference


class YearReceiptArtifact(StrictContractModel):
    """A durable response receipt, distinct from complete acquisition success."""

    version: Literal[1] = 1
    kind: Literal["internal-dukascopy-year-receipt"] = "internal-dukascopy-year-receipt"
    requested_at: str
    recorded_at: str
    period_start: str
    period_end: str
    receipt: YearReceipt


def validate_dukascopy_year_receipt(files: Mapping[str, bytes], *, runtime: Path, runs: Path) -> YearReceiptArtifact:
    """Replay one saved receipt against its committed raw and original runtime."""
    expected = {"year-receipt.json", "approval.yaml", "profile.yaml"}
    if set(files) != expected | {"index.json"} or json.loads(files["index.json"]) != {
        name: sha256(files[name]).hexdigest() for name in sorted(expected)
    }:
        raise ValueError("fx_year_receipt_inventory_or_hash_mismatch")
    if (
        sha256(files["approval.yaml"]).hexdigest() != APPROVAL_SHA256
        or sha256(files["profile.yaml"]).hexdigest() != PROFILE_SHA256
    ):
        raise ValueError("fx_year_receipt_configuration_mismatch")
    artifact = YearReceiptArtifact.model_validate_json(files["year-receipt.json"])
    receipt = artifact.receipt
    reference = receipt.raw_reference
    requested = _timestamp(artifact.requested_at)
    received = _timestamp(receipt.received_at)
    recorded = _timestamp(artifact.recorded_at)
    targets = urls(date.fromisoformat(artifact.period_start), date.fromisoformat(artifact.period_end), requested)
    years = range(date.fromisoformat(artifact.period_start).year, date.fromisoformat(artifact.period_end).year + 1)
    if dict(zip(years, targets, strict=True)).get(receipt.year) != receipt.url or not requested <= received <= recorded:
        raise ValueError("fx_year_receipt_target_or_time_mismatch")
    database = _safe_path(runtime / DATABASE_FILENAME)
    if not database.is_file():
        raise ValueError("fx_year_receipt_runtime_missing")
    repo = ProductionRequestRepository(database)
    logical = repo.logical_request(reference.logical_request_id)
    fingerprint = sha256(
        (str(targets) + artifact.period_start + artifact.period_end + APPROVAL_SHA256 + PROFILE_SHA256).encode()
    ).hexdigest()
    if logical is None or (
        logical.task_id,
        logical.source_id,
        logical.operation,
        logical.created_at,
        logical.request_fingerprint,
        logical.source_approval_version,
        logical.source_profile_version,
    ) != (reference.task_id, "dukascopy", "fx-daily-bid", artifact.requested_at, fingerprint, 1, 1):
        raise ValueError("fx_year_receipt_logical_mismatch")
    identity = repo.physical_attempt_identity(reference.physical_attempt_id)
    publication = repo.raw_publication_record(reference.publication_id)
    if (
        identity is None
        or identity[0] != reference.logical_request_id
        or identity[2] != reference.publication_generation
        or repo.physical_attempt_state(reference.physical_attempt_id) != ("succeeded", reference.publication_generation)
        or publication is None
        or publication.state != "committed"
        or publication.intent.source_id != "dukascopy"
        or publication.intent.operation != "fx-daily-bid"
        or reference not in repo.committed_raw_references(reference.logical_request_id)
        or reference.raw_schema_id != "dukascopy-fx-daily-raw"
        or reference.raw_schema_version != 1
    ):
        raise ValueError("fx_year_receipt_committed_reference_mismatch")
    raw = _safe_path(runs) / reference.task_id / reference.relative_path
    body = _read_file(raw / "body.bin")
    request = json.loads(_read_file(raw / "request.json"))
    response = json.loads(_read_file(raw / "response.json"))
    saved_receipt = json.loads(_read_file(raw / "receipt.json"))
    if (
        sha256(body).hexdigest() != reference.content_sha256
        or len(body) != reference.byte_count
        or request
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
        | {"source_id": "dukascopy", "operation": "fx-daily-bid"}
        or response
        != {
            "byte_count": reference.byte_count,
            "media_type": publication.intent.media_type,
            "encoding": publication.intent.encoding,
            "raw_schema_id": reference.raw_schema_id,
            "raw_schema_version": reference.raw_schema_version,
        }
        or saved_receipt != {"body_sha256": reference.content_sha256, "byte_count": reference.byte_count}
    ):
        raise ValueError("fx_year_receipt_saved_raw_mismatch")
    parse_daily(body, receipt.url, receipt.year, received)
    return artifact


class FxMetadataV2(StrictContractModel):
    """Source semantics and acquisition provenance for the complete period."""

    version: Literal[2] = 2
    source: Literal["dukascopy"] = "dukascopy"
    observation_basis: Literal["USDJPY UTC daily Bid close"] = "USDJPY UTC daily Bid close"
    period_start: str
    period_end: str
    requested_at: str
    receipts: tuple[YearReceipt, ...]
    year_requested_at: dict[str, str] | None = None
    package_version: Literal["1.50.0"] = "1.50.0"
    bridge_version: Literal[1] = 1
    node_version: str


class FxIndexV2(StrictContractModel):
    """All years validated; calendar-specific completeness belongs to the join."""

    version: Literal[2] = 2
    analysis_ready: Literal[False] = False
    status: Literal["accepted_with_limitations"] = "accepted_with_limitations"
    files: dict[str, str]
    excluded_dates: dict[str, str]


def normalized(files: Mapping[str, bytes], metadata: FxMetadataV2) -> DailySeries:
    """Reparse every source year and verify raw receipts without any HTTP."""
    now = datetime.fromisoformat(metadata.requested_at)
    start, end = date.fromisoformat(metadata.period_start), date.fromisoformat(metadata.period_end)
    years = range(start.year, end.year + 1)
    if metadata.year_requested_at is None:
        requested = dict.fromkeys(years, now)
        expected = dict(zip(years, urls(start, end, now), strict=True))
    else:
        if start > end or set(metadata.year_requested_at) != {str(year) for year in years}:
            raise ValueError("fx_component_request_inventory_mismatch")
        requested = {year: _timestamp(metadata.year_requested_at[str(year)]) for year in years}
        if min(requested.values()) != _timestamp(metadata.requested_at):
            raise ValueError("fx_component_request_start_mismatch")
        expected = {
            year: urls(max(start, date(year, 1, 1)), min(end, date(year, 12, 31)), requested[year])[0] for year in years
        }
    if len(metadata.receipts) != len(expected) or {r.year for r in metadata.receipts} != set(expected):
        raise ValueError("fx_year_inventory_mismatch")
    rows: list[DailyRow] = []
    for receipt in sorted(metadata.receipts, key=lambda r: r.year):
        received = datetime.fromisoformat(receipt.received_at)
        if (
            received.tzinfo is None
            or received < requested[receipt.year]
            or receipt.url != expected[receipt.year]
            or not date(2026, 9, 26) <= received.date() < date(2026, 12, 26)
        ):
            raise ValueError("fx_receipt_invalid")
        body = files[f"raw/{receipt.year}.bin"]
        ref = receipt.raw_reference
        if (
            ref.raw_schema_id != "dukascopy-fx-daily-raw"
            or ref.raw_schema_version != 1
            or ref.byte_count != len(body)
            or ref.content_sha256 != sha256(body).hexdigest()
        ):
            raise ValueError("fx_raw_reference_mismatch")
        rows.extend(parse_daily(body, receipt.url, receipt.year, received).rows)
    return DailySeries(rows=tuple(rows))


def _index(files: Mapping[str, bytes]) -> FxIndexV2:
    meta = FxMetadataV2.model_validate_json(files["metadata.json"])
    if (
        sha256(files["approval.yaml"]).hexdigest() != APPROVAL_SHA256
        or sha256(files["profile.yaml"]).hexdigest() != PROFILE_SHA256
    ):
        raise ValueError("fx_configuration_mismatch")
    series = normalized(files, meta)
    if DailySeries.model_validate_json(files["normalized.json"]) != series:
        raise ValueError("fx_normalization_mismatch")
    expected = {"metadata.json", "normalized.json", "approval.yaml", "profile.yaml"} | {
        f"raw/{r.year}.bin" for r in meta.receipts
    }
    if set(files) - {"index.json"} != expected:
        raise ValueError("fx_inventory_mismatch")
    excluded = {
        r.on: "unfinished_candle" if not r.confirmed else "outside_fixed_period"
        for r in series.rows
        if not r.confirmed or not meta.period_start <= r.on <= meta.period_end
    }
    return FxIndexV2(
        files={name: sha256(files[name]).hexdigest() for name in sorted(expected)}, excluded_dates=excluded
    )


def validate_dukascopy_fx(files: Mapping[str, bytes]) -> None:
    """Reproduce a version-two index from exact saved bytes."""
    if FxIndexV2.model_validate_json(files["index.json"]) != _index(files):
        raise ValueError("fx_index_mismatch")


def acquire_dukascopy_fx(
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
    sleep: Callable[[float], None] = time.sleep,
) -> FxIndexV2:
    """Send each annual GET once, current year first; stop on any failure."""
    config, runtime, runs = map(_safe_path, (config, runtime, runs))
    output = _safe_path(runs / destination)
    if (
        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", destination)
        or output.parent != runs
        or output.exists()
        or destination == task_id
    ):
        raise ValueError("fx_destination_invalid_or_exists")
    now = clock()
    targets = urls(start, end, now)
    binding = load_dukascopy_binding(config, now.date())
    runtime.mkdir(mode=0o700, parents=True, exist_ok=True)
    runs.mkdir(mode=0o700, parents=True, exist_ok=True)
    if runs.stat().st_uid != os.geteuid() or stat.S_IMODE(runs.stat().st_mode) != 0o700:
        raise ValueError("fx_runs_root_requires_private_permissions")
    files = {
        name: (config / relative).read_bytes()
        for name, relative in (
            ("approval.yaml", "source-approvals/dukascopy/v1.yaml"),
            ("profile.yaml", "source-profiles/dukascopy/v1.yaml"),
        )
    }
    repo = initialize_runtime_storage(runtime)
    uid = uuid4().hex
    logical = ProductionLogicalRequest(
        logical_request_id=f"dukascopy-{uid}",
        task_id=task_id,
        source_id="dukascopy",
        operation="fx-daily-bid",
        request_fingerprint=sha256(
            (str(targets) + start.isoformat() + end.isoformat() + APPROVAL_SHA256 + PROFILE_SHA256).encode()
        ).hexdigest(),
        source_approval_version=1,
        source_profile_version=1,
        credential_scope_alias=None,
        egress_scope="default-egress",
        created_at=now.isoformat(),
    )
    lease = repo.acquire_lease(
        f"dukascopy-owner-{uid}", now, RuntimeLeasePolicy(lease_duration_seconds=120, heartbeat_interval_seconds=30)
    )
    release_allowed = True
    receipts = []
    receipt_root = runs / f"year-receipts-{uid}"
    try:
        receipt_root.mkdir(mode=0o700)
        repo.admit_logical_request(
            logical, binding.profile.rate_domain, ProductionCachePolicy(applicable=False), lease, clock(), QueuePolicy()
        )
        claimed = repo.claim_next_queued(binding.profile.rate_domain, lease, clock())
        if claimed is None or claimed.logical_request_id != logical.logical_request_id:
            raise ValueError("dukascopy_request_not_claimed")
        years = list(zip(range(start.year, end.year + 1), targets, strict=True))
        years.sort(key=lambda pair: (pair[0] != now.year, pair[0]))
        for sequence, (year, url) in enumerate(years, 1):
            if sequence > 1:
                sleep(2)
                lease = repo.heartbeat_lease(
                    lease, clock(), RuntimeLeasePolicy(lease_duration_seconds=120, heartbeat_interval_seconds=30)
                )
            intent = intent_for_url(url, year, now)
            received: list[datetime] = []
            physical = DukascopyPhysicalTransport(
                intent,
                DukascopyHttpClientPolicy(
                    connect_timeout_seconds=10,
                    read_timeout_seconds=30,
                    write_timeout_seconds=10,
                    pool_timeout_seconds=10,
                ),
                transport,
                clock,
                received.append,
            )
            coordinator = ProductionTransportCoordinator(repo, physical, lambda: f"permit-{uuid4().hex}")
            attempt = ProductionPhysicalAttempt(
                physical_attempt_id=f"dukascopy-attempt-{uid}-{year}",
                logical_request_id=logical.logical_request_id,
                sequence_number=sequence,
                lease_generation=lease.generation,
                created_at=clock().isoformat(),
            )

            def send(
                request: PhysicalTransportRequest, _envelope: object, physical: DukascopyPhysicalTransport = physical
            ) -> tuple[UntrustedTransportResponse, object]:
                try:
                    load_dukascopy_binding(config, clock().date())
                except ValueError:
                    raise ReceivedResponseValidationError("dukascopy_binding_changed") from None
                return physical(request), None

            try:
                exchange = coordinator.execute_exchange_with_callback(
                    intent.to_transport_request(logical.logical_request_id, attempt.physical_attempt_id),
                    attempt,
                    f"reservation-{uid}-{year}",
                    gate_keys(task_id),
                    gate_policy(),
                    TransportValidationPolicy(
                        allowed_media_types=("application/json", "text/plain", "text/html"),
                        allowed_encodings=("utf-8",),
                    ),
                    lease,
                    clock(),
                    clock,
                    None,
                    send,
                )
            except RuntimeError as error:
                if str(error).startswith("gate_blocked:"):
                    coordinator.finalize_logical_request(
                        logical.logical_request_id,
                        LogicalResultOutcome.FAILED,
                        lease,
                        clock(),
                        "dukascopy_gate_blocked",
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
            received_at = received[0]

            def validate(body: bytes, url: str = url, year: int = year, received_at: datetime = received_at) -> None:
                parse_daily(body, url, year, received_at)

            try:
                validate(candidate.body)
            except ValueError:
                coordinator.finalize_logical_request(
                    logical.logical_request_id,
                    LogicalResultOutcome.FAILED,
                    lease,
                    clock(),
                    "dukascopy_source_validation_failed",
                )
                publish_preparation(
                    runs,
                    destination,
                    {
                        **files,
                        "unaccepted-body.bin": candidate.body,
                        "failure.json": json.dumps(
                            {
                                "year": year,
                                "url": url,
                                "received_at": received_at.isoformat(),
                                "logical_request_id": logical.logical_request_id,
                                "reason": "source_validation_failed",
                            }
                        ).encode(),
                    },
                    validator=lambda _files: None,
                )
                raise
            release_allowed = False
            reference = RawArtifactPublisher(runs, repo).publish(
                candidate,
                RawPublicationIntent(
                    publication_id=f"publication-{uid}-{year}",
                    task_id=task_id,
                    logical_request_id=logical.logical_request_id,
                    physical_attempt_id=attempt.physical_attempt_id,
                    source_id="dukascopy",
                    operation="fx-daily-bid",
                    content_sha256=candidate.sha256,
                    byte_count=len(candidate.body),
                    media_type=candidate.media_type,
                    encoding=candidate.encoding,
                    raw_schema_id="dukascopy-fx-daily-raw",
                    raw_schema_version=1,
                    publication_generation=lease.generation,
                ),
                lease,
                clock(),
                clock(),
                validate,
            )
            files[f"raw/{year}.bin"] = candidate.body
            year_receipt = YearReceipt(year=year, url=url, received_at=received_at.isoformat(), raw_reference=reference)
            receipts.append(year_receipt)
            release_allowed = True
            artifact = YearReceiptArtifact(
                requested_at=now.isoformat(),
                recorded_at=clock().isoformat(),
                period_start=start.isoformat(),
                period_end=end.isoformat(),
                receipt=year_receipt,
            )
            receipt_files = {name: files[name] for name in ("approval.yaml", "profile.yaml")}
            receipt_files["year-receipt.json"] = artifact.model_dump_json(indent=2).encode()
            receipt_files["index.json"] = json.dumps(
                {name: sha256(content).hexdigest() for name, content in sorted(receipt_files.items())}, indent=2
            ).encode()

            def validate_year(saved: Mapping[str, bytes]) -> None:
                validate_dukascopy_year_receipt(saved, runtime=runtime, runs=runs)

            try:
                publish_preparation(receipt_root, str(year), receipt_files, validator=validate_year)
            except OSError, ValueError, RuntimeError:
                coordinator.finalize_logical_request(
                    logical.logical_request_id,
                    LogicalResultOutcome.FAILED,
                    lease,
                    clock(),
                    "dukascopy_year_receipt_publication_failed",
                )
                raise
        metadata = FxMetadataV2(
            period_start=start.isoformat(),
            period_end=end.isoformat(),
            requested_at=now.isoformat(),
            receipts=tuple(receipts),
            node_version=bridge(
                {
                    "command": "urls",
                    "start": start.isoformat(),
                    "end": (end + timedelta(days=1)).isoformat(),
                    "now": now.isoformat(),
                }
            )["node_version"],
        )
        files["metadata.json"] = metadata.model_dump_json(indent=2, exclude_none=True).encode()
        files["normalized.json"] = normalized(files, metadata).model_dump_json(indent=2).encode()
        index = _index(files)
        files["index.json"] = index.model_dump_json(indent=2).encode()
        release_allowed = False
        publish_preparation(runs, destination, files, validator=validate_dukascopy_fx)
        coordinator.finalize_logical_request(
            logical.logical_request_id, LogicalResultOutcome.SUCCEEDED, lease, clock(), None
        )
        release_allowed = True
        return index
    finally:
        if release_allowed and clock() < datetime.fromisoformat(lease.expires_at):
            repo.release_lease(lease, clock())
