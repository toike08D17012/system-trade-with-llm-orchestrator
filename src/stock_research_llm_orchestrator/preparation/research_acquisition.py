"""Shared standard acquisition for baseline preparation and individual research roles."""

import io
import json
import time
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime
from hashlib import sha256
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx
import pandas as pd  # type: ignore[import-untyped]
import yaml

from stock_research_llm_orchestrator.contracts.base import FileReferenceV1
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.acquisition_analysis import (
    AcquiredEvidence,
    AcquisitionRequest,
    AcquisitionResult,
)
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.policies import SourceApprovalV1, SourceProfileV1
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.task import DetailedAnalysisTaskV1
from stock_research_llm_orchestrator.preparation.market_evidence import (
    JPX_MEDIA_TYPE,
    JPX_WORKBOOK_URL,
    _acquisition_metadata,
)
from stock_research_llm_orchestrator.preparation.public_ir_follow_up import acquire_public_document
from stock_research_llm_orchestrator.preparation.research_follow_up import extract_text
from stock_research_llm_orchestrator.sources.jpx.current_list import JpxCurrentListAdapter
from stock_research_llm_orchestrator.sources.jpx.verification import verify_jpx_security
from stock_research_llm_orchestrator.sources.protocol import BoundedSourceResponse
from stock_research_llm_orchestrator.sources.yfinance.adapter import YfinanceDailyAdapter
from stock_research_llm_orchestrator.sources.yfinance.mapping import map_jpx_verification_to_yfinance_daily
from stock_research_llm_orchestrator.sources.yfinance.native_history import (
    HistoryResult,
    HistoryThrottledError,
    NativeHistoryClient,
)
from stock_research_llm_orchestrator.sources.yfinance.normalization import (
    NormalizedPrices,
    normalize_history,
    three_year_start,
)


def _json(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def request_sha256(request: AcquisitionRequest) -> str:
    """Bind the exact structured request independently of the requesting role."""
    return sha256(request.model_dump_json().encode()).hexdigest()


def _reference(path: str, body: bytes) -> FileReferenceV1:
    return FileReferenceV1(
        artifact_id="acquisition-" + sha256(path.encode()).hexdigest()[:24],
        relative_path=path,
        media_type="application/octet-stream",
        sha256=sha256(body).hexdigest(),
    )


def _market_text(normalized: NormalizedPrices, metadata: dict[str, object], symbol: str) -> dict[str, object]:
    last = normalized.rows[-1] if normalized.rows else None
    identity = (
        metadata["response_symbol"] == symbol
        and metadata["exchange_timezone"] == "Asia/Tokyo"
        and metadata["instrument_type"] == "EQUITY"
    )
    return {
        "normalized_prices": normalized.model_dump(mode="json"),
        "quality_passed": normalized.quality_passed,
        "provider_identity_verified": identity,
        "latest_observation": last.model_dump(mode="json") if last else None,
        "latest_price_verified": False,
        "price_validation_pending": ["jpx_snapshot_freshness_unconfirmed", "trading_calendar_unconfirmed"],
        "provenance": metadata,
        "limitations": [
            "Returned yfinance table; not exchange-primary HTTP bytes.",
            "Dividend currency and current listing freshness are not independently verified.",
        ],
    }


def validate_acquisition(request: AcquisitionRequest, result: AcquisitionResult, files: Mapping[str, bytes]) -> None:
    """Verify every retained binding without network, provider state, or credentials."""
    if result.request_sha256 != request_sha256(request):
        raise ValueError("acquisition_request_hash_mismatch")
    for reference in (*result.files, *result.attempt_references):
        if (
            reference.relative_path not in files
            or sha256(files[reference.relative_path]).hexdigest() != reference.sha256
        ):
            raise ValueError("acquisition_file_hash_mismatch")
    if result.reuse_reference is not None:
        ref = result.reuse_reference
        if ref.relative_path not in files or sha256(files[ref.relative_path]).hexdigest() != ref.sha256:
            raise ValueError("acquisition_reuse_hash_mismatch")
        if ref.relative_path.endswith("jpx-metadata.json"):
            provenance = json.loads(files[ref.relative_path])
            listing_evidence = next(item for item in result.evidence if item.source_id == "jpx")
            if (
                provenance["sha256"] != listing_evidence.raw_reference.sha256
                or provenance["retrieved_at"] != listing_evidence.retrieved_at
                or not provenance["physical_attempt_id"]
            ):
                raise ValueError("acquisition_listing_reuse_provenance_mismatch")
        else:
            original = AcquisitionResult.model_validate_json(files[ref.relative_path])
            if (
                original.evidence != result.evidence
                or original.files != result.files
                or original.status != result.status
                or original.validation != result.validation
            ):
                raise ValueError("acquisition_reuse_provenance_mismatch")
            audit_ref = next(item for item in original.files if item.relative_path.endswith("attempts.json"))
            original_audit = json.loads(files[audit_ref.relative_path])
            original_request = AcquisitionRequest.model_validate_json(json.dumps(original_audit["request"]))
            if original_request.model_copy(update={"purpose": request.purpose}) != request:
                raise ValueError("acquisition_reuse_target_mismatch")
            validate_acquisition(original_request, original, files)
    if result.status == "acquired":
        expected_sources = {
            "listing": {"jpx"},
            "market": {"jpx", "yfinance"},
            "public_document": {request.classification},
        }
        if {item.source_id for item in result.evidence} != expected_sources[request.operation]:
            raise ValueError("acquisition_operation_source_mismatch")
    for evidence in result.evidence:
        ref = evidence.raw_reference
        if ref not in result.files or sha256(files[ref.relative_path]).hexdigest() != ref.sha256:
            raise ValueError("acquisition_evidence_hash_mismatch")
        if datetime.fromisoformat(evidence.retrieved_at) > datetime.fromisoformat(result.completed_at):
            raise ValueError("acquisition_evidence_from_future")
        if request.operation in {"listing", "market"} and evidence.source_id == "jpx":
            if not ref.relative_path.endswith("jpx.xlsx"):
                raise ValueError("acquisition_listing_original_missing")
            snapshot = JpxCurrentListAdapter().parse(
                BoundedSourceResponse(
                    physical_attempt_id="offline-acquisition",
                    body=files[ref.relative_path],
                    sha256=ref.sha256,
                    media_type=JPX_MEDIA_TYPE,
                    encoding="binary",
                )
            )
            expected = verify_jpx_security(snapshot, request.security_code).model_dump(mode="json")
            if evidence.validation != ("verified" if expected["status"] == "verified_eligible" else "rejected"):
                raise ValueError("acquisition_listing_validation_mismatch")
            if json.loads(evidence.text) != {
                "verification": expected,
                "freshness": "unconfirmed",
                "limitation": "Current published snapshot does not establish listing status at other dates.",
            }:
                raise ValueError("acquisition_listing_reconstruction_mismatch")
        elif evidence.source_id == "yfinance":
            if evidence.validation != "pending":
                raise ValueError("acquisition_market_validation_mismatch")
            text = json.loads(evidence.text)
            normalized_ref = next(item for item in result.files if item.relative_path.endswith("normalized.json"))
            normalized = NormalizedPrices.model_validate_json(files[normalized_ref.relative_path])
            metadata_ref = next(item for item in result.files if item.relative_path.endswith("history-metadata.json"))
            metadata = json.loads(files[metadata_ref.relative_path])
            frame = pd.read_csv(io.BytesIO(files[ref.relative_path]), index_col=0, dtype=str)
            frame.index = pd.to_datetime(frame.index, utc=True).tz_convert("Asia/Tokyo")
            start = date.fromisoformat(metadata["arguments"]["start"])
            end = date.fromisoformat(metadata["arguments"]["end"])
            if (
                (request.start is not None and start != request.start)
                or (request.end is not None and end != request.end)
                or end > datetime.fromisoformat(result.requested_at).astimezone(ZoneInfo("Asia/Tokyo")).date()
            ):
                raise ValueError("acquisition_market_period_mismatch")
            listing_ref = next(item for item in result.files if item.relative_path.endswith("jpx.xlsx"))
            listing = JpxCurrentListAdapter().parse(
                BoundedSourceResponse(
                    physical_attempt_id="offline-listing",
                    body=files[listing_ref.relative_path],
                    sha256=listing_ref.sha256,
                    media_type=JPX_MEDIA_TYPE,
                    encoding="binary",
                )
            )
            intent = map_jpx_verification_to_yfinance_daily(
                verify_jpx_security(listing, request.security_code), start=start, end=end
            )
            _acquisition_metadata(HistoryResult(frame, metadata), intent.symbol, start, end)
            reconstructed = normalize_history(
                frame, start=start, end=date.fromisoformat(metadata["arguments"]["end"]), currency=metadata["currency"]
            )
            if text != _market_text(normalized, metadata, metadata["symbol"]) or reconstructed != normalized:
                raise ValueError("acquisition_market_reconstruction_mismatch")
        else:
            if evidence.validation != "verified":
                raise ValueError("acquisition_document_validation_mismatch")
            media_ref = next(item for item in result.files if item.relative_path.endswith("document-metadata.json"))
            metadata = json.loads(files[media_ref.relative_path])
            if (
                metadata["url"] != request.url
                or metadata["classification"] != request.classification
                or evidence.source_id != request.classification
                or evidence.source_reference != request.url
                or metadata["retrieved_at"] != evidence.retrieved_at
                or extract_text(files[ref.relative_path], metadata["media_type"]) != evidence.text
            ):
                raise ValueError("acquisition_document_reconstruction_mismatch")


class ResearchAcquisitionExecutor:
    """Acquire neutral raw evidence through shared durable source communication gates."""

    def __init__(
        self,
        *,
        task: DetailedAnalysisTaskV1,
        runtime: Path,
        runs: Path,
        evaluated_at: datetime,
        approval_root: Path = Path("config/source-approvals"),
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        transport: httpx.BaseTransport | None = None,
        adapter: YfinanceDailyAdapter | None = None,
        wait_for_gate: bool = False,
        sleeper: Callable[[float], None] = time.sleep,
        retained_acquisitions: tuple[tuple[AcquisitionRequest, AcquisitionResult, Mapping[str, bytes]], ...] = (),
    ) -> None:
        """Use one runtime across all roles, tasks, and source requests."""
        self.wait_for_gate = wait_for_gate
        self.sleeper = sleeper
        self.task = task
        self.runtime = runtime
        self.runs = runs
        self.evaluated_at = evaluated_at
        self.approval_root = approval_root
        self.clock = clock
        self.transport = transport
        self.adapter = adapter or YfinanceDailyAdapter(history_client=NativeHistoryClient(runtime / "yfinance-native"))
        self._listing: tuple[bytes, str, str] | None = None
        self._cache: dict[str, tuple[AcquisitionResult, dict[str, bytes]]] = {}
        for request, result, payload in retained_acquisitions:
            if request.security_code != self.task.security.security_code:
                raise ValueError("acquisition_retained_security_mismatch")
            validate_acquisition(request, result, payload)
            if result.status != "acquired":
                raise ValueError("acquisition_retained_original_required")
            original = result
            while (
                not original.attempt_references
                and original.reuse_reference is not None
                and original.reuse_reference.relative_path.endswith("reused-result.json")
            ):
                original = AcquisitionResult.model_validate_json(payload[original.reuse_reference.relative_path])
            key = sha256(_json({**request.model_dump(mode="json"), "purpose": "shared-raw-target"})).hexdigest()
            self._cache[key] = original, dict(payload)

    def __call__(self, request: AcquisitionRequest, role: str, turn: int) -> tuple[AcquisitionResult, dict[str, bytes]]:
        """Wait only for known gate deadlines, preserving every deferred outcome."""
        history: dict[str, bytes] = {}
        iteration = 0
        while True:
            result, files = self._execute_once(request, role, turn)
            if not self.wait_for_gate or result.status != "waiting":
                if history:
                    files = {**history, **files}
                    result = result.model_copy(
                        update={"files": (*result.files, *(_reference(n, b) for n, b in history.items()))}
                    )
                validate_acquisition(request, result, files)
                return result, files
            audits = [json.loads(body) for name, body in files.items() if name.endswith("attempts.json")]
            deadlines = [
                event["retry_at"] for audit in audits for event in audit["events"] if event.get("retry_at") is not None
            ]
            if not deadlines:
                return result, files
            retry_at = max(float(value) for value in deadlines)
            prefix = f"gate-waits/{iteration}/"
            history.update({prefix + name: body for name, body in files.items()})
            history[prefix + "result.json"] = result.model_dump_json().encode()
            iteration += 1
            while self.clock().timestamp() < retry_at:
                delay = min(60.0, retry_at - self.clock().timestamp())
                print(f"research_acquisition: waiting for shared source gate ({delay:.0f}s)", flush=True)
                self.sleeper(delay)

    def _execute_once(
        self, request: AcquisitionRequest, role: str, turn: int
    ) -> tuple[AcquisitionResult, dict[str, bytes]]:
        """Return an individual actual acquisition or gate outcome."""
        request = AcquisitionRequest.model_validate(request.model_dump())
        if request.security_code != self.task.security.security_code:
            raise ValueError("acquisition_security_mismatch")
        digest = request_sha256(request)
        key = sha256(_json({**request.model_dump(mode="json"), "purpose": "shared-raw-target"})).hexdigest()
        started = self.clock()
        files: dict[str, bytes]
        if key in self._cache:
            original, payload = self._cache[key]
            original_bytes = original.model_dump_json().encode()
            path = f"acquisition/{digest}/reused-result.json"
            files = {**payload, path: original_bytes}
            result = original.model_copy(
                update={
                    "request_sha256": digest,
                    "requested_at": started.isoformat(),
                    "completed_at": started.isoformat(),
                    "attempt_references": (),
                    "reuse_reference": _reference(path, original_bytes),
                    "detail": "Reused retained original; no independent network acquisition.",
                }
            )
            validate_acquisition(request, result, files)
            return result, files
        prefix = f"acquisition/{digest}"
        files = {}
        events: list[dict[str, object]] = []
        evidence: tuple[AcquiredEvidence, ...] = ()
        status: Literal["not_attempted", "waiting", "failed", "acquired"] = "not_attempted"
        validation: Literal["pending", "verified", "rejected"] = "pending"
        detail = ""
        try:
            if request.operation == "public_document":
                evidence = (self._document(request, prefix, files, events),)
            elif request.operation == "listing":
                evidence = (self._get_listing(request, prefix, files, events),)
            else:
                listing = self._get_listing(request, prefix, files, events)
                evidence = (listing, self._market(request, prefix, files, events))
            status = "acquired"
            validation = (
                "rejected"
                if any(item.validation == "rejected" for item in evidence)
                else "verified"
                if all(item.validation == "verified" for item in evidence)
                else "pending"
            )
            detail = "Original acquired; individual quality and point-in-time limitations remain in evidence."
        except HistoryThrottledError as error:
            status, detail = "waiting", str(error)
            for event in events:
                if event.get("event") == "native_started":
                    event["event"] = "native_gate_not_admitted"
            events.append({"event": "native_gate_wait", "retry_at": error.retry_at, "reason": detail})
        except (ValueError, RuntimeError, OSError) as error:
            detail = str(error) if isinstance(error, (ValueError, RuntimeError)) else type(error).__name__
            sent = any(item.get("event") in {"transport_started", "native_started"} for item in events)
            gate = next((item for item in reversed(events) if item.get("event") == "gate_result"), {})
            status = (
                "waiting"
                if any(item.get("event") == "gate_wait" for item in events)
                or gate.get("status") in {"deferred", "waiting", "blocked"}
                else "failed"
                if sent
                else "not_attempted"
            )
        audit_path = prefix + "/attempts.json"
        files[audit_path] = _json(
            {
                "request": request.model_dump(mode="json"),
                "role": role,
                "turn": turn,
                "events": events,
                "status": status,
                "detail": detail,
            }
        )
        result = AcquisitionResult(
            request_sha256=digest,
            status=status,
            validation=validation,
            requested_at=started.isoformat(),
            completed_at=self.clock().isoformat(),
            attempt_references=(_reference(audit_path, files[audit_path]),)
            if any(item.get("event") in {"transport_started", "native_started"} for item in events)
            else (),
            files=tuple(_reference(name, body) for name, body in files.items()),
            evidence=evidence if status == "acquired" else (),
            reuse_reference=_reference(prefix + "/jpx-metadata.json", files[prefix + "/jpx-metadata.json"])
            if status == "acquired"
            and not any(item.get("event") in {"transport_started", "native_started"} for item in events)
            else None,
            detail=detail,
        )
        validate_acquisition(request, result, files)
        if status == "acquired":
            self._cache[key] = result, files
        return result, files

    def _approval(self, source: str, version: int) -> None:
        approval = SourceApprovalV1.model_validate(
            yaml.safe_load((self.approval_root / source / f"v{version}.yaml").read_bytes())
        )
        day = self.clock().date().isoformat()
        if (
            approval.status != "approved"
            or not approval.online_use_allowed
            or not approval.external_agent_transfer_allowed
            or approval.effective_on is None
            or approval.recheck_due_on is None
            or not approval.effective_on <= day < approval.recheck_due_on
        ):
            raise ValueError("acquisition_source_not_approved")
        if source in {"jpx", "public-web"}:
            profile_version = 2 if source == "jpx" else 1
            profile = SourceProfileV1.model_validate(
                yaml.safe_load(
                    (self.approval_root.parent / f"source-profiles/{source}/v{profile_version}.yaml").read_bytes()
                )
            )
            if (
                profile.source_approval_reference.sha256
                != sha256((self.approval_root / source / f"v{version}.yaml").read_bytes()).hexdigest()
                or not profile.enabled
            ):
                raise ValueError("acquisition_profile_binding_invalid")

    def _get_listing(
        self, request: AcquisitionRequest, prefix: str, files: dict[str, bytes], events: list[dict[str, object]]
    ) -> AcquiredEvidence:
        self._approval("jpx", 3)
        if self._listing is None:

            def validate_listing(body: bytes, media: str) -> None:
                JpxCurrentListAdapter().parse(
                    BoundedSourceResponse(
                        physical_attempt_id="listing-body-check",
                        body=body,
                        sha256=sha256(body).hexdigest(),
                        media_type=media,
                        encoding="binary",
                    )
                )

            receipt = acquire_public_document(
                url=JPX_WORKBOOK_URL,
                allowed_host="www.jpx.co.jp",
                runtime=self.runtime / "http",
                runs=self.runs,
                task_id=self.task.task_id,
                source_id="jpx",
                approval_version=3,
                profile_version=2,
                media_types=(JPX_MEDIA_TYPE,),
                operation="current-listed-issues",
                raw_schema_id="jpx-current-listed-issues-xlsx",
                clock=self.clock,
                transport=self.transport,
                audit=events.append,
                validate_body=validate_listing,
            )
            self._listing = receipt.body, receipt.retrieved_at, receipt.physical_attempt_id
        else:
            events.append({"event": "listing_raw_reused", "independent_network_acquisition": False})
        body, retrieved, attempt = self._listing
        snapshot = JpxCurrentListAdapter().parse(
            BoundedSourceResponse(
                physical_attempt_id=attempt,
                body=body,
                sha256=sha256(body).hexdigest(),
                media_type=JPX_MEDIA_TYPE,
                encoding="binary",
            )
        )
        if (
            date.fromisoformat(snapshot.snapshot_on)
            > datetime.fromisoformat(retrieved).astimezone(ZoneInfo("Asia/Tokyo")).date()
        ):
            raise ValueError("acquisition_listing_snapshot_in_future")
        verification = verify_jpx_security(snapshot, request.security_code)
        raw_path = prefix + "/jpx.xlsx"
        files[raw_path] = body
        files[prefix + "/jpx-metadata.json"] = _json(
            {
                "retrieved_at": retrieved,
                "physical_attempt_id": attempt,
                "source_url": JPX_WORKBOOK_URL,
                "source_approval_version": 3,
                "source_profile_version": 2,
                "sha256": sha256(body).hexdigest(),
            }
        )
        files[prefix + "/jpx-approval.yaml"] = (self.approval_root / "jpx/v3.yaml").read_bytes()
        files[prefix + "/jpx-profile.yaml"] = (self.approval_root.parent / "source-profiles/jpx/v2.yaml").read_bytes()
        return AcquiredEvidence(
            evidence_id="jpx-" + sha256(body).hexdigest()[:16],
            source_id="jpx",
            source_reference=JPX_WORKBOOK_URL,
            retrieved_at=retrieved,
            raw_reference=_reference(raw_path, body),
            text=json.dumps(
                {
                    "verification": verification.model_dump(mode="json"),
                    "freshness": "unconfirmed",
                    "limitation": "Current published snapshot does not establish listing status at other dates.",
                },
                ensure_ascii=False,
            ),
            validation="verified" if verification.status == "verified_eligible" else "rejected",
        )

    def _document(
        self, request: AcquisitionRequest, prefix: str, files: dict[str, bytes], events: list[dict[str, object]]
    ) -> AcquiredEvidence:
        assert request.url is not None
        source = request.classification or "public-web"
        self._approval("public-web", 1)
        policy_path = self.approval_root / "public-web/v1.yaml"
        files[prefix + "/owner-public-policy.yaml"] = policy_path.read_bytes()
        receipt = acquire_public_document(
            url=request.url,
            allowed_host=urlsplit(request.url).hostname or "",
            runtime=self.runtime / "http",
            runs=self.runs,
            task_id=self.task.task_id,
            source_id="public-web",
            raw_schema_id="public-web-document-raw",
            clock=self.clock,
            transport=self.transport,
            audit=events.append,
        )
        raw_path = prefix + "/document.bin"
        files[raw_path] = receipt.body
        files[prefix + "/document-metadata.json"] = _json(
            {
                "url": request.url,
                "media_type": receipt.media_type,
                "retrieved_at": receipt.retrieved_at,
                "physical_attempt_id": receipt.physical_attempt_id,
                "classification": source,
                "authorization": "repository-owner-public-web-acquisition-policy-2026-11-14",
            }
        )
        return AcquiredEvidence(
            evidence_id=source + "-" + sha256((request.url + receipt.sha256).encode()).hexdigest()[:24],
            source_id=source,
            source_reference=request.url,
            retrieved_at=receipt.retrieved_at,
            raw_reference=_reference(raw_path, receipt.body),
            text=extract_text(receipt.body, receipt.media_type),
            validation="verified",
        )

    def _market(
        self, request: AcquisitionRequest, prefix: str, files: dict[str, bytes], events: list[dict[str, object]]
    ) -> AcquiredEvidence:
        self._approval("yfinance", 3)
        assert self._listing is not None
        body, retrieved, attempt = self._listing
        snapshot = JpxCurrentListAdapter().parse(
            BoundedSourceResponse(
                physical_attempt_id=attempt,
                body=body,
                sha256=sha256(body).hexdigest(),
                media_type=JPX_MEDIA_TYPE,
                encoding="binary",
            )
        )
        today = self.evaluated_at.astimezone(ZoneInfo("Asia/Tokyo")).date()
        end = request.end or today
        start = request.start or three_year_start(end)
        if end > today or start >= end:
            raise ValueError("acquisition_market_uncompleted_or_invalid_period")
        intent = map_jpx_verification_to_yfinance_daily(
            verify_jpx_security(snapshot, request.security_code), start=start, end=end
        )
        events.append(
            {
                "event": "native_started",
                "symbol": intent.symbol,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "rate_unit": "library_call_not_http_send",
            }
        )
        history = self.adapter.acquire_history(intent)
        metadata = _acquisition_metadata(history, intent.symbol, start, end)
        events.append({"event": "native_completed", "retrieved_at": metadata["retrieved_at"]})
        normalized = normalize_history(
            history.frame,
            start=start,
            end=end,
            currency=metadata["currency"] if isinstance(metadata["currency"], str) else None,
        )
        raw_path = prefix + "/prices.csv"
        files[raw_path] = history.frame.to_csv(lineterminator="\n").encode()
        files[prefix + "/history-metadata.json"] = _json(metadata)
        files[prefix + "/normalized.json"] = normalized.model_dump_json().encode()
        text = _market_text(normalized, metadata, intent.symbol)
        return AcquiredEvidence(
            evidence_id="yfinance-" + sha256(files[raw_path] + b"\0" + _json(text)).hexdigest()[:24],
            source_id="yfinance",
            source_reference="yfinance:Ticker.history:" + intent.symbol,
            retrieved_at=str(metadata["retrieved_at"]),
            raw_reference=_reference(raw_path, files[raw_path]),
            text=json.dumps(text, ensure_ascii=False),
            validation="pending",
        )
