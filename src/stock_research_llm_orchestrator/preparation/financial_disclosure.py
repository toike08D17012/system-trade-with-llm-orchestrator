"""Offline filing preparation; source-native candidates never imply analysis readiness."""

import json
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Literal
from uuid import uuid4

import yaml

from stock_research_llm_orchestrator.contracts.base import Identifier, Sha256Hex, StrictContractModel
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.evidence import ApplicablePeriodV1
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.policies import (
    DetailedAnalysisPolicyV1,
    SourceApprovalV1,
)
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.task import DetailedAnalysisTaskV1
from stock_research_llm_orchestrator.preparation.financial_candidates import correction_reasons
from stock_research_llm_orchestrator.preparation.fx_evidence import read_bundle
from stock_research_llm_orchestrator.preparation.market_revalidation import _safe_path, _timestamp
from stock_research_llm_orchestrator.preparation.price_fx_run import (
    PriceFxPreparationManifest,
    publish_run_preparation,
    validate_price_fx_run,
)
from stock_research_llm_orchestrator.preparation.run_requirements import TOKYO
from stock_research_llm_orchestrator.requests.production import RawPublicationIntent
from stock_research_llm_orchestrator.sources.edinet.document_list import EdinetDocument, EdinetDocumentListAdapter
from stock_research_llm_orchestrator.sources.edinet.xbrl_document import EdinetXbrlDocumentAdapter
from stock_research_llm_orchestrator.sources.protocol import BoundedSourceResponse, CredentialFreeSourceIntent


class FilingInput(StrictContractModel):
    """Operator-exported acquisition metadata; not a claim of database commitment."""

    key: Identifier
    publication: RawPublicationIntent
    source_intent: CredentialFreeSourceIntent
    retrieved_at: str
    acquisition_approval_sha256: Sha256Hex


class IssuerBinding(StrictContractModel):
    """Explicit operator mapping grounded in an exact retained list observation."""

    security_code: Identifier
    edinet_code: Identifier
    provider_security_code: Identifier
    applicable_period: ApplicablePeriodV1
    list_key: Identifier
    list_sha256: Sha256Hex


class FinancialInput(StrictContractModel):
    """Input inventory for an offline preparation, with no credential fields."""

    version: Literal[1] = 1
    checked_at: str
    survey_period: ApplicablePeriodV1
    issuer: IssuerBinding | None
    acquisitions: tuple[FilingInput, ...]


class FilingObservation(StrictContractModel):
    """A filing observation retains its list identity and every exclusion reason."""

    list_key: Identifier
    document: EdinetDocument
    reasons: tuple[str, ...]
    archive_keys: tuple[str, ...]


class FinancialManifest(StrictContractModel):
    """Internal candidate-only result; never a frozen public evidence set."""

    version: Literal[1] = 1
    kind: Literal["internal-financial-disclosure-preparation"] = "internal-financial-disclosure-preparation"
    status: Literal["pending"] = "pending"
    analysis_ready: Literal[False] = False
    task_id: Identifier
    checked_at: str
    generation: Identifier
    reasons: tuple[str, ...]
    filings: tuple[FilingObservation, ...]
    observed_list_dates: tuple[str, ...]
    missing_list_dates: tuple[str, ...]
    annual_periods: tuple[tuple[str, str], ...]
    interim_periods: tuple[tuple[str, str], ...]
    required_annual_count: Literal[5] = 5
    required_interim_count: Literal[8] = 8
    price_fx_status: str | None
    hashes: dict[str, Sha256Hex]
    limitations: tuple[str, ...] = (
        "source_native_candidates_not_normalized_financials",
        "operator_exported_acquisition_metadata_not_verified_against_runtime_database",
        "no_external_agent_transfer",
    )


def _json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode()


def _subset(files: Mapping[str, bytes], prefix: str) -> dict[str, bytes]:
    return {name[len(prefix) :]: body for name, body in files.items() if name.startswith(prefix)}


def _response(files: Mapping[str, bytes], item: FilingInput) -> BoundedSourceResponse:
    p = item.publication
    prefix = f"raw/{item.key}/"
    raw = _subset(files, prefix)
    if set(raw) != {"body.bin", "request.json", "response.json", "receipt.json"}:
        raise ValueError("financial_raw_inventory_invalid")
    request_fields = (
        "logical_request_id",
        "operation",
        "physical_attempt_id",
        "publication_generation",
        "publication_id",
        "source_id",
        "task_id",
    )
    response_fields = ("byte_count", "encoding", "media_type", "raw_schema_id", "raw_schema_version")
    values = p.model_dump()
    if (
        json.loads(raw["request.json"]) != {key: values[key] for key in request_fields}
        or json.loads(raw["response.json"]) != {key: values[key] for key in response_fields}
        or json.loads(raw["receipt.json"]) != {"body_sha256": p.content_sha256, "byte_count": p.byte_count}
        or len(raw["body.bin"]) != p.byte_count
        or p.source_id != "edinet"
        or item.source_intent.source_id != "edinet"
        or p.operation != item.source_intent.operation
    ):
        raise ValueError("financial_raw_binding_invalid")
    return BoundedSourceResponse(
        physical_attempt_id=p.physical_attempt_id,
        body=raw["body.bin"],
        sha256=p.content_sha256,
        media_type=p.media_type,
        encoding=p.encoding,
    )


def evaluate_financial(files: Mapping[str, bytes]) -> tuple[FinancialManifest, bytes]:
    """Reparse all exact inputs and reconstruct candidate and manifest bytes."""
    inputs = FinancialInput.model_validate_json(files["inputs.json"])
    task = DetailedAnalysisTaskV1.model_validate_json(files["task.json"])
    checked = _timestamp(inputs.checked_at)
    policy = DetailedAnalysisPolicyV1.model_validate(yaml.safe_load(files["evaluation-policy.yaml"]))
    if policy.policy_version != task.evaluation_policy_version or _timestamp(policy.effective_at) > checked:
        raise ValueError("financial_evaluation_policy_mismatch")
    if _timestamp(task.task_accepted_at) > checked:
        raise ValueError("financial_task_from_future")
    keys = [item.key for item in inputs.acquisitions]
    if len(keys) != len(set(keys)):
        raise ValueError("financial_duplicate_input_key")
    approval_bytes = files["approval.yaml"]
    approval = SourceApprovalV1.model_validate(yaml.safe_load(approval_bytes))
    if approval.source_id != "edinet" or approval.external_agent_transfer_allowed:
        raise ValueError("financial_source_policy_invalid")
    reasons = {
        "financial_mapping_unimplemented",
        "issuer_ir_unchecked",
        "other_disclosures_unchecked",
        "latest_filings_unconfirmed",
    }
    today = checked.astimezone(TOKYO).date().isoformat()
    if (
        approval.status != "approved"
        or approval.effective_on is None
        or approval.recheck_due_on is None
        or not approval.effective_on <= today < approval.recheck_due_on
    ):
        reasons.add("edinet_local_policy_unavailable")
    observations: list[tuple[str, EdinetDocument]] = []
    dates: set[str] = set()
    archives: dict[str, list[str]] = {}
    candidates: dict[str, object] = {}
    entities: dict[str, set[str]] = {}
    expected = {"inputs.json", "task.json", "approval.yaml", "generation.json", "evaluation-policy.yaml"}
    for item in inputs.acquisitions:
        response = _response(files, item)
        expected.update(
            f"raw/{item.key}/{name}" for name in ("body.bin", "request.json", "response.json", "receipt.json")
        )
        acquisition_name = f"acquisition-approvals/{item.key}.yaml"
        expected.add(acquisition_name)
        saved_approval = files[acquisition_name]
        previous = SourceApprovalV1.model_validate(yaml.safe_load(saved_approval))
        retrieved = _timestamp(item.retrieved_at)
        if (
            sha256(saved_approval).hexdigest() != item.acquisition_approval_sha256
            or previous.source_id != "edinet"
            or retrieved > checked
        ):
            raise ValueError("financial_acquisition_metadata_invalid")
        acquired_day = retrieved.astimezone(TOKYO).date().isoformat()
        if (
            previous.status != "approved"
            or previous.effective_on is None
            or previous.recheck_due_on is None
            or not previous.effective_on <= acquired_day < previous.recheck_due_on
        ):
            reasons.add("acquisition_approval_unavailable")
        p = item.publication
        intent = item.source_intent
        if p.operation == "document-list":
            adapter = EdinetDocumentListAdapter()
            if adapter.build_intent(intent.operation, intent.parameters) != intent:
                raise ValueError("financial_list_intent_invalid")
            if (p.raw_schema_id, p.raw_schema_version) != ("edinet-document-list-raw", 1):
                raise ValueError("financial_list_schema_invalid")
            listing = adapter.parse(response)
            if listing.requested_date != next(p.value for p in intent.parameters if p.name == "date"):
                raise ValueError("financial_list_date_mismatch")
            processed = datetime.strptime(listing.processed_at, "%Y-%m-%d %H:%M").replace(tzinfo=TOKYO)
            if processed > retrieved or listing.requested_date > today:
                raise ValueError("financial_list_from_future")
            dates.add(listing.requested_date)
            observations.extend((item.key, doc) for doc in listing.documents)
        elif p.operation == "document-retrieval":
            xbrl = EdinetXbrlDocumentAdapter()
            if xbrl.build_intent(intent.operation, intent.parameters) != intent:
                raise ValueError("financial_archive_intent_invalid")
            if (p.raw_schema_id, p.raw_schema_version) != ("edinet-xbrl-zip-raw", 1):
                raise ValueError("financial_archive_schema_invalid")
            parsed = xbrl.parse(response)
            entities[item.key] = {context.entity_identifier for context in parsed.facts.contexts}
            archives.setdefault(intent.resource_key, []).append(item.key)
            candidates[item.key] = parsed.model_dump(mode="json")
        else:
            raise ValueError("financial_operation_unsupported")
    binding = inputs.issuer
    bound = binding is not None and binding.security_code == task.security.security_code
    if binding is not None:
        bound = bound and binding.applicable_period.start_date <= today <= binding.applicable_period.end_date
        bound = bound and any(
            key == binding.list_key
            and doc.edinet_code == binding.edinet_code
            and doc.security_code == binding.provider_security_code
            for key, doc in observations
        )
        bound = bound and any(
            i.key == binding.list_key and i.publication.content_sha256 == binding.list_sha256
            for i in inputs.acquisitions
        )
    if (
        binding is not None
        and len({d.edinet_code for _, d in observations if d.security_code == binding.provider_security_code}) > 1
    ):
        bound = False
    if not bound:
        reasons.add("issuer_binding_unresolved")
    filings = []
    annual: set[tuple[str, str]] = set()
    interim: set[tuple[str, str]] = set()
    documents = [doc for _, doc in observations]
    if set(archives) - {doc.document_id for doc in documents}:
        reasons.add("archive_without_filing_list")
    for key, doc in observations:
        excluded = set(correction_reasons(doc, documents))
        if not bound or binding is None:
            excluded.add("issuer_binding_unresolved")
        elif doc.edinet_code != binding.edinet_code or doc.security_code != binding.provider_security_code:
            excluded.add("other_or_unresolved_issuer")
        submitted = datetime.strptime(doc.submitted_at, "%Y-%m-%d %H:%M").replace(tzinfo=TOKYO)
        list_acquisition = next(i for i in inputs.acquisitions if i.key == key)
        if submitted > _timestamp(list_acquisition.retrieved_at):
            raise ValueError("financial_list_before_submission")
        if submitted > checked:
            excluded.add("filing_after_check")
        if doc.withdrawal_status == "1":
            excluded.add("withdrawn")
        if not doc.xbrl_available:
            excluded.add("xbrl_unavailable")
        archive_keys = tuple(archives.get(doc.document_id, []))
        if not archive_keys:
            excluded.add("archive_missing")
        if len({i.publication.content_sha256 for i in inputs.acquisitions if i.key in archive_keys}) > 1:
            excluded.add("archive_snapshots_conflict")
        for archive_key in archive_keys:
            if binding is None or entities[archive_key] != {binding.edinet_code}:
                excluded.add("xbrl_entity_unresolved")
            acquisition = next(i for i in inputs.acquisitions if i.key == archive_key)
            if _timestamp(acquisition.retrieved_at) < submitted:
                raise ValueError("financial_archive_before_submission")
        snapshots = {d.model_dump_json() for d in documents if d.document_id == doc.document_id}
        if len(snapshots) > 1:
            excluded.add("filing_snapshots_conflict")
        if doc.period_start is None or doc.period_end is None:
            excluded.add("period_missing")
        elif doc.period_start > doc.period_end:
            raise ValueError("financial_period_reversed")
        elif doc.period_end > today:
            excluded.add("period_after_check")
        elif not excluded:
            target = annual if doc.document_type.value in {"120", "130"} else interim
            target.add((doc.period_start, doc.period_end))
        filings.append(
            FilingObservation(list_key=key, document=doc, reasons=tuple(sorted(excluded)), archive_keys=archive_keys)
        )
    if len(annual) < 5:
        reasons.add("annual_periods_insufficient")
    if len(interim) < 8:
        reasons.add("interim_periods_insufficient")

    day = date.fromisoformat(inputs.survey_period.start_date)
    end = date.fromisoformat(inputs.survey_period.end_date)
    if end.isoformat() > today:
        raise ValueError("financial_survey_from_future")
    missing = []
    while day <= end:
        if day.isoformat() not in dates:
            missing.append(day.isoformat())
        day += timedelta(days=1)
    if missing:
        reasons.add("list_survey_gaps")
    price_files = _subset(files, "price-fx/")
    price_status = None
    if price_files:
        validate_price_fx_run(price_files)
        price = PriceFxPreparationManifest.model_validate_json(price_files["manifest.json"])
        if (
            task != DetailedAnalysisTaskV1.model_validate_json(price_files["task.json"])
            or _timestamp(price.checked_at) != checked
        ):
            raise ValueError("financial_price_fx_task_or_time_mismatch")
        price_status = price.status
        expected.update(f"price-fx/{name}" for name in price_files)
    else:
        reasons.add("price_fx_not_connected")
    if set(files) - {"manifest.json", "candidates.json"} != expected:
        raise ValueError("financial_input_inventory_invalid")
    generation = json.loads(files["generation.json"])
    candidate_bytes = _json(candidates)
    hashes = {name: sha256(files[name]).hexdigest() for name in sorted(expected)}
    hashes["candidates.json"] = sha256(candidate_bytes).hexdigest()
    manifest = FinancialManifest(
        task_id=task.task_id,
        checked_at=inputs.checked_at,
        generation=generation,
        reasons=tuple(sorted(reasons)),
        filings=tuple(filings),
        observed_list_dates=tuple(sorted(dates)),
        missing_list_dates=tuple(missing),
        annual_periods=tuple(sorted(annual)),
        interim_periods=tuple(sorted(interim)),
        price_fx_status=price_status,
        hashes=hashes,
    )
    return manifest, candidate_bytes


def validate_financial(files: Mapping[str, bytes]) -> None:
    """Verify every retained byte, candidate, and decision by offline replay."""
    result, candidates = evaluate_financial(files)
    if (
        FinancialManifest.model_validate_json(files["manifest.json"]) != result
        or files["candidates.json"] != candidates
    ):
        raise ValueError("financial_replay_mismatch")


def prepare_financial(source: Path, output: Path) -> FinancialManifest:
    """Publish an operator-assembled input directory without network or credentials."""
    source, output = _safe_path(source), _safe_path(output)
    if output.exists():
        raise FileExistsError("financial_output_exists")
    if source == output or source in output.parents or output in source.parents:
        raise ValueError("financial_output_overlaps_input")
    files = read_bundle(source)
    if {"manifest.json", "candidates.json", "generation.json"} & files.keys():
        raise ValueError("financial_input_contains_output")
    files["generation.json"] = _json(f"financial-{uuid4().hex}")
    result, candidates = evaluate_financial(files)
    files["candidates.json"] = candidates
    files["manifest.json"] = result.model_dump_json(indent=2).encode()
    publish_run_preparation(output.parent, output.name, files, validator=validate_financial)
    return result
