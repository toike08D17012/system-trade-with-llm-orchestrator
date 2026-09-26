"""Owner-approved two-request EDINET acceptance, with committed metadata export."""

import json
import os
import stat
import time
from collections.abc import Callable
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import httpx

from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.evidence import ApplicablePeriodV1
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.task import DetailedAnalysisTaskV1
from stock_research_llm_orchestrator.credentials.models import CredentialFilePolicy
from stock_research_llm_orchestrator.credentials.preflight import preflight_credential_file
from stock_research_llm_orchestrator.preparation.edinet_revalidation import revalidate_list, select_target
from stock_research_llm_orchestrator.preparation.financial_disclosure import (
    FilingInput,
    FinancialInput,
    IssuerBinding,
    prepare_financial,
)
from stock_research_llm_orchestrator.preparation.fx_evidence import read_bundle
from stock_research_llm_orchestrator.preparation.market_revalidation import _safe_path, _timestamp
from stock_research_llm_orchestrator.preparation.run_requirements import TOKYO
from stock_research_llm_orchestrator.preparation.storage import publish_preparation
from stock_research_llm_orchestrator.requests.production import (
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
    TemporaryRawCandidate,
    TransportValidationPolicy,
    UntrustedTransportResponse,
)
from stock_research_llm_orchestrator.sources.edinet.document_list import EdinetDocumentListAdapter
from stock_research_llm_orchestrator.sources.edinet.httpx_transport import (
    EdinetHttpClientPolicy,
    build_httpx_edinet_transport,
)
from stock_research_llm_orchestrator.sources.edinet.production_policy import (
    APPROVAL_SHA256,
    PROFILE_SHA256,
    gate_keys,
    gate_policy,
    load_edinet_binding,
)
from stock_research_llm_orchestrator.sources.edinet.transport import EdinetPhysicalTransport
from stock_research_llm_orchestrator.sources.edinet.xbrl_document import EdinetXbrlDocumentAdapter
from stock_research_llm_orchestrator.sources.protocol import BoundedSourceResponse, SourceParameter


def acquire_edinet_acceptance(
    *,
    task: DetailedAnalysisTaskV1,
    config: Path,
    runtime: Path,
    runs: Path,
    credential: Path,
    retained_list: Path | None = None,
    allow_network: bool = False,
    allow_credential: bool = False,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    sleep: Callable[[float], None] = time.sleep,
    transport: httpx.BaseTransport | None = None,
) -> Path:
    """Acquire the fixed list and, only if unique, its target annual archive once."""
    if not allow_network or not allow_credential:
        raise ValueError("edinet_explicit_opt_in_required")
    if task.security.security_code != "7203" or task.security.mic != "XTKS":
        raise ValueError("edinet_acceptance_task_out_of_scope")
    if _timestamp(task.task_accepted_at) > clock():
        raise ValueError("edinet_task_from_future")
    config, runtime, runs = map(_safe_path, (config, runtime, runs))
    if runtime == runs or runtime in runs.parents or runs in runtime.parents:
        raise ValueError("edinet_storage_overlap")
    if preflight_credential_file(credential, policy=CredentialFilePolicy(required_mode=0o600)).status != "ready":
        raise ValueError("edinet_credential_preflight_failed")
    retained = read_bundle(_safe_path(retained_list)) if retained_list is not None else {}
    recovered = None
    if retained:
        recovered, listing = revalidate_list(retained)
        select_target(retained["body.bin"], listing)
        if _timestamp(recovered.received_at) > clock():
            raise ValueError("edinet_retained_list_from_future")
    binding = load_edinet_binding(config, clock().astimezone(TOKYO).date())
    for path in (runtime, runs):
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        if path.stat().st_uid != os.geteuid() or stat.S_IMODE(path.stat().st_mode) != 0o700:
            raise ValueError("edinet_storage_requires_private_permissions")
    repo = initialize_runtime_storage(runtime)
    uid = uuid4().hex
    lease_policy = RuntimeLeasePolicy(lease_duration_seconds=120, heartbeat_interval_seconds=30)
    lease = repo.acquire_lease(f"edinet-owner-{uid}", clock(), lease_policy)
    release_allowed = True
    approval = (config / "source-approvals/edinet/v2.yaml").read_bytes()
    files = {
        "task.json": task.model_dump_json(indent=2).encode(),
        "approval.yaml": approval,
        "evaluation-policy.yaml": (
            config / "policies/detailed-analysis" / f"v{task.evaluation_policy_version}.yaml"
        ).read_bytes(),
    }
    acquisitions: list[FilingInput] = []
    selected = None
    source_intent = EdinetDocumentListAdapter().build_intent(
        "document-list", (SourceParameter(name="date", value="2026-06-10"), SourceParameter(name="type", value="2"))
    )
    if retained:
        selected = select_target(retained["body.bin"], listing)
        files.update({f"retained-list/{name}": body for name, body in retained.items()})
        source_intent = EdinetXbrlDocumentAdapter().build_intent(
            "document-retrieval",
            (SourceParameter(name="document_id", value=selected.document_id), SourceParameter(name="type", value="1")),
        )
    try:
        for sequence in range(1 if retained else 0, 2):
            key = "list" if sequence == 0 else "document"
            if sequence:
                # Keep the lease alive while respecting all shared 60-second gates.
                for _ in range(3):
                    sleep(21)
                    lease = repo.heartbeat_lease(lease, clock(), lease_policy)
            load_edinet_binding(config, clock().astimezone(TOKYO).date())
            logical = ProductionLogicalRequest(
                logical_request_id=f"edinet-{uid}-{key}",
                task_id=task.task_id,
                source_id="edinet",
                operation=source_intent.operation,
                request_fingerprint=sha256(
                    (source_intent.model_dump_json() + APPROVAL_SHA256 + PROFILE_SHA256).encode()
                ).hexdigest(),
                source_approval_version=2,
                source_profile_version=2,
                credential_scope_alias="edinet-api-key",
                egress_scope="default-egress",
                created_at=clock().isoformat(),
            )
            repo.admit_logical_request(
                logical,
                binding.profile.rate_domain,
                ProductionCachePolicy(applicable=False),
                lease,
                clock(),
                QueuePolicy(),
            )
            claimed = repo.claim_next_queued(binding.profile.rate_domain, lease, clock())
            if claimed is None or claimed.logical_request_id != logical.logical_request_id:
                release_allowed = False
                raise ValueError("edinet_request_not_claimed")
            physical = build_httpx_edinet_transport(
                source_intent,
                credential,
                EdinetHttpClientPolicy(
                    connect_timeout_seconds=10,
                    read_timeout_seconds=30,
                    write_timeout_seconds=10,
                    pool_timeout_seconds=10,
                ),
                transport=transport,
            )
            coordinator = ProductionTransportCoordinator(repo, physical, lambda: f"permit-{uuid4().hex}")
            attempt = ProductionPhysicalAttempt(
                physical_attempt_id=f"edinet-attempt-{uid}-{key}",
                logical_request_id=logical.logical_request_id,
                sequence_number=1,
                lease_generation=lease.generation,
                created_at=clock().isoformat(),
            )
            received: list[datetime] = []

            def send(
                request: PhysicalTransportRequest,
                _envelope: object,
                physical: EdinetPhysicalTransport = physical,
                received: list[datetime] = received,
            ) -> tuple[UntrustedTransportResponse, object]:
                load_edinet_binding(config, clock().astimezone(TOKYO).date())
                response = physical(request)
                received.append(clock())
                return response, None

            release_allowed = False
            try:
                exchange = coordinator.execute_exchange_with_callback(
                    source_intent.to_transport_request(logical.logical_request_id, attempt.physical_attempt_id),
                    attempt,
                    f"reservation-{uid}-{key}",
                    gate_keys(task.task_id, source_intent.operation),
                    gate_policy(),
                    TransportValidationPolicy(
                        allowed_media_types=("application/json",) if sequence == 0 else ("application/octet-stream",),
                        allowed_encodings=("utf-8",) if sequence == 0 else ("binary",),
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
                        logical.logical_request_id, LogicalResultOutcome.FAILED, lease, clock(), "edinet_gate_blocked"
                    )
                    release_allowed = True
                raise
            result = exchange.execution
            if result.status != "succeeded" or result.candidate is None:
                coordinator.finalize_logical_request(
                    logical.logical_request_id, LogicalResultOutcome(result.status), lease, clock(), result.reason_code
                )
                release_allowed = True
                raise ValueError("edinet_exchange_failed")
            candidate = result.candidate

            def validate(body: bytes, candidate: TemporaryRawCandidate = candidate, sequence: int = sequence) -> None:
                response = BoundedSourceResponse(
                    physical_attempt_id=candidate.physical_attempt_id,
                    body=body,
                    sha256=candidate.sha256,
                    media_type=candidate.media_type,
                    encoding=candidate.encoding,
                )
                if sequence == 0:
                    EdinetDocumentListAdapter().parse(response)
                else:
                    EdinetXbrlDocumentAdapter().parse(response)

            try:
                validate(candidate.body)
            except ValueError:
                coordinator.finalize_logical_request(
                    logical.logical_request_id,
                    LogicalResultOutcome.FAILED,
                    lease,
                    clock(),
                    "edinet_source_validation_failed",
                )
                publish_preparation(
                    runs,
                    f"edinet-unaccepted-{uid}-{key}",
                    {
                        "body.bin": candidate.body,
                        "failure.json": json.dumps(
                            {
                                "reason": "source_validation_failed",
                                "key": key,
                                "sha256": candidate.sha256,
                                "received_at": received[0].isoformat(),
                                "source_intent": source_intent.model_dump(mode="json"),
                            }
                        ).encode(),
                    },
                    validator=lambda _files: None,
                )
                release_allowed = True
                raise
            publication = RawPublicationIntent(
                publication_id=f"publication-{uid}-{key}",
                task_id=task.task_id,
                logical_request_id=logical.logical_request_id,
                physical_attempt_id=attempt.physical_attempt_id,
                source_id="edinet",
                operation=source_intent.operation,
                content_sha256=candidate.sha256,
                byte_count=len(candidate.body),
                media_type=candidate.media_type,
                encoding=candidate.encoding,
                raw_schema_id="edinet-document-list-raw" if sequence == 0 else "edinet-xbrl-zip-raw",
                raw_schema_version=1,
                publication_generation=lease.generation,
            )
            reference = RawArtifactPublisher(runs, repo).publish(
                candidate, publication, lease, clock(), clock(), validate
            )
            coordinator.finalize_logical_request(
                logical.logical_request_id, LogicalResultOutcome.SUCCEEDED, lease, clock(), None
            )
            release_allowed = True
            files.update(
                {
                    f"raw/{key}/{name}": body
                    for name, body in read_bundle(runs / task.task_id / reference.relative_path).items()
                }
            )
            files[f"acquisition-approvals/{key}.yaml"] = approval
            acquisitions.append(
                FilingInput(
                    key=key,
                    publication=publication,
                    source_intent=source_intent,
                    retrieved_at=received[0].isoformat(),
                    acquisition_approval_sha256=APPROVAL_SHA256,
                )
            )
            if sequence == 0:
                listing = EdinetDocumentListAdapter().parse(BoundedSourceResponse.from_candidate(candidate))
                if listing.requested_date != "2026-06-10":
                    raise ValueError("edinet_acceptance_list_date_mismatch")
                selected = select_target(candidate.body, listing)
                source_intent = EdinetXbrlDocumentAdapter().build_intent(
                    "document-retrieval",
                    (
                        SourceParameter(name="document_id", value=selected.document_id),
                        SourceParameter(name="type", value="1"),
                    ),
                )
        assert selected is not None and selected.edinet_code is not None
        checked = clock().isoformat()
        day = clock().astimezone(TOKYO).date().isoformat()
        inputs = FinancialInput(
            checked_at=checked,
            survey_period=ApplicablePeriodV1(start_date="2026-06-10", end_date="2026-06-10"),
            issuer=IssuerBinding(
                security_code="7203",
                edinet_code=selected.edinet_code,
                provider_security_code="72030",
                applicable_period=ApplicablePeriodV1(start_date="2026-06-10", end_date=day),
                list_key="list",
                list_sha256=recovered.sha256 if recovered is not None else acquisitions[0].publication.content_sha256,
            ),
            acquisitions=tuple(acquisitions),
            retained_list=bool(retained),
        )
        files["inputs.json"] = inputs.model_dump_json(indent=2).encode()
        name = f"edinet-input-{uid}"
        publish_preparation(runs, name, files, validator=lambda _files: None)
        output = runs / f"edinet-prepared-{uid}"
        prepare_financial(runs / name, output)
        return output
    finally:
        if release_allowed and clock() < _timestamp(lease.expires_at):
            repo.release_lease(lease, clock())
