"""Build private P1 annual records without claiming freshness or evidence freezing."""

import json
from collections import Counter
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from hashlib import sha256

import yaml
from pydantic import TypeAdapter

from stock_research_llm_orchestrator.contracts.base import ArtifactReferenceV1
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.evidence import (
    ApplicablePeriodV1,
    CalculatedEvidenceV1,
    EvidenceRecordV1,
    NormalizedEvidenceV1,
    RawEvidenceV1,
    SourceMetadataEvidenceV1,
)
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.policies import SourceApprovalV1
from stock_research_llm_orchestrator.preparation.financial_acceptance import FinancialAdoptionPolicy
from stock_research_llm_orchestrator.preparation.financial_comparative import AnnualPeriod, ComparativePeriodValues
from stock_research_llm_orchestrator.preparation.financial_disclosure import (
    FilingInput,
    FinancialInput,
    FinancialManifest,
)
from stock_research_llm_orchestrator.preparation.financial_mapping import METRICS, Metric
from stock_research_llm_orchestrator.preparation.financial_pair_acceptance import PairAdoptionPolicy
from stock_research_llm_orchestrator.preparation.run_requirements import TOKYO


def _bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, indent=2).encode()


def _hash(value: object) -> str:
    return sha256(_bytes(value)).hexdigest()


def _source(
    dependency: str, files: Mapping[str, bytes], policy: FinancialAdoptionPolicy, *, pair: bool
) -> tuple[SourceMetadataEvidenceV1, RawEvidenceV1, dict[str, dict[str, str]], dict[str, object]]:
    """Resolve one retained archive and its acquisition-time approval, never a DB claim."""
    if pair:
        acquisitions = tuple(FilingInput.model_validate_json(_bytes(a)) for a in json.loads(files["acquisitions.json"]))
        rows = [r for r in json.loads(files["retained-list/body.bin"])["results"] if r["docID"] == policy.document_id]
        submitted = [r["submitDateTime"] for r in rows]
    else:
        acquisitions = FinancialInput.model_validate_json(files["inputs.json"]).acquisitions
        manifest = FinancialManifest.model_validate_json(files["manifest.json"])
        submitted = [f.document.submitted_at for f in manifest.filings if f.document.document_id == policy.document_id]
    selected = [
        a
        for a in acquisitions
        if a.publication.content_sha256 == policy.archive_sha256
        and {p.name: p.value for p in a.source_intent.parameters}.get("document_id") == policy.document_id
    ]
    if len(selected) != 1 or len(submitted) != 1:
        raise ValueError("annual_evidence_source_ambiguous")
    acquisition = selected[0]
    raw_path = f"raw/{acquisition.key}/body.bin"
    approval_path = "approval.yaml" if pair else f"acquisition-approvals/{acquisition.key}.yaml"
    if (
        sha256(files[raw_path]).hexdigest() != policy.archive_sha256
        or sha256(files[approval_path]).hexdigest() != acquisition.acquisition_approval_sha256
    ):
        raise ValueError("annual_evidence_source_hash_mismatch")
    approval = SourceApprovalV1.model_validate(yaml.safe_load(files[approval_path]))
    published = datetime.strptime(submitted[0], "%Y-%m-%d %H:%M").replace(tzinfo=TOKYO).isoformat()
    source_payload = {
        "layer": "source_metadata",
        "source_id": approval.source_id,
        "source_approval_reference": ArtifactReferenceV1(
            artifact_type="source_approval",
            artifact_id=approval.artifact_id,
            schema_id=approval.schema_id,
            schema_version=approval.schema_version,
            sha256=acquisition.acquisition_approval_sha256,
        ).model_dump(mode="json"),
        "provider": approval.provider,
        "service": approval.service,
        "dataset": approval.dataset,
        "published_at": published,
        "first_available_at": None,
        "updated_at": None,
        "retrieved_at": acquisition.retrieved_at,
        "applicable_period": ApplicablePeriodV1(
            start_date=policy.start_date.isoformat(), end_date=policy.end_date.isoformat()
        ).model_dump(mode="json"),
        "timezone": "Asia/Tokyo",
        "reference": f"https://api.edinet-fsa.go.jp/api/v2/documents/{policy.document_id}?type=1",
        "content_sha256": policy.archive_sha256,
        "data_version": policy.archive_sha256,
    }
    provenance: dict[str, object] = {"metadata": source_payload, "acquisition": acquisition.model_dump(mode="json")}
    source = SourceMetadataEvidenceV1.model_validate_json(
        _bytes({"evidence_id": "source-" + _hash(provenance), **source_payload})
    )
    payload_id = "zip-" + policy.archive_sha256
    raw_payload = {
        "layer": "raw",
        "source_metadata_evidence_id": source.evidence_id,
        "payload_reference": ArtifactReferenceV1(
            artifact_type="raw_evidence_payload",
            artifact_id=payload_id,
            schema_id="detailed-analysis.raw-evidence-payload",
            schema_version=1,
            sha256=policy.archive_sha256,
        ).model_dump(mode="json"),
        "content_sha256": policy.archive_sha256,
        "data_version": policy.archive_sha256,
    }
    raw = RawEvidenceV1.model_validate_json(_bytes({"evidence_id": "raw-" + _hash(raw_payload), **raw_payload}))
    locators = {
        payload_id: {"dependency": dependency, "path": raw_path, "sha256": policy.archive_sha256},
        approval.artifact_id: {
            "dependency": dependency,
            "path": approval_path,
            "sha256": acquisition.acquisition_approval_sha256,
        },
    }
    return source, raw, locators, provenance


def build_annual_evidence(
    task_id: str,
    required: tuple[AnnualPeriod, ...],
    reports: Mapping[str, tuple[ComparativePeriodValues, ...]],
    dependencies: Mapping[str, Mapping[str, bytes]],
    calculations: bytes,
    limitations: tuple[str, ...],
) -> tuple[bytes, dict[str, object]]:
    """Map replayed annual adoption to P1 record models in an unfrozen local draft."""
    records: dict[str, EvidenceRecordV1] = {}
    locators: dict[str, list[dict[str, str]]] = {}
    raw_ids = {}
    payloads: dict[str, object] = {}
    for key in sorted(reports):
        pair = key == "pair_adoption"
        prefix = key.removesuffix("comparative") if not pair else ""
        dependency = "pair" if pair else prefix + "financial"
        policy = (
            PairAdoptionPolicy.model_validate_json(dependencies[key]["policy.json"]).amended
            if pair
            else FinancialAdoptionPolicy.model_validate_json(dependencies[prefix + "adoption"]["policy.json"])
        )
        source, raw, source_locators, provenance = _source(dependency, dependencies[dependency], policy, pair=pair)
        payloads[source.evidence_id] = provenance
        records[source.evidence_id] = source
        records[raw.evidence_id] = raw
        raw_ids[key] = raw.evidence_id
        for artifact_id, locator in source_locators.items():
            if locator not in locators.setdefault(artifact_id, []):
                locators[artifact_id].append(locator)
    normalized_ids = {}
    for period in sorted(required, key=lambda p: p.start_date):
        for metric in METRICS:
            facts = [
                (key, fact)
                for key, report in sorted(reports.items())
                for record in report
                if record.period == period
                for fact in record.values
                if fact.metric == metric
            ]
            observations = [
                {
                    "dependency": key,
                    "values_sha256": sha256(dependencies[key]["values.json"]).hexdigest(),
                    "fact": fact.model_dump(mode="json"),
                }
                for key, fact in facts
            ]
            accepted = [(key, fact) for key, fact in facts if fact.status == "accepted"]
            if any(fact.value is None for _, fact in accepted):
                raise ValueError("annual_evidence_accepted_value_invalid")
            numbers = {Decimal(fact.value) for _, fact in accepted if fact.value is not None}
            if len(numbers) > 1 or any(not n.is_finite() for n in numbers):
                raise ValueError("annual_evidence_accepted_value_invalid")
            value = None
            if numbers:
                number = next(iter(numbers))
                text = format(number, "f")
                value = (text.rstrip("0").rstrip(".") if "." in text else text) if number else "0"
            inputs = sorted({raw_ids[key] for key, _ in (accepted or facts)} or set(raw_ids.values()))
            missing_reason = None if value is not None else "required_input_not_accepted"
            payload = {
                "metric": metric,
                "period": period.model_dump(mode="json"),
                "scope": "consolidated",
                "value": value,
                "missing_reason": missing_reason,
                "unit": "JPY",
                "input_evidence_ids": inputs,
                "observations": observations,
                "normalization_logic_version": 1,
            }
            digest = _hash(payload)
            normalized = NormalizedEvidenceV1(
                evidence_id="normalized-" + digest,
                layer="normalized",
                input_evidence_ids=tuple(inputs),
                value=value,
                missing_reason=missing_reason,
                unit="JPY",
                content_sha256=digest,
                data_version=digest,
                normalization_logic_version=1,
            )
            records[normalized.evidence_id] = normalized
            normalized_ids[period, metric] = normalized.evidence_id
            payloads[normalized.evidence_id] = payload
    calculated = json.loads(calculations)
    numerator_metrics: dict[str, Metric] = {
        "operating_profit_to_revenue": "operating_profit",
        "parent_profit_to_revenue": "parent_profit",
        "operating_cash_flow_to_revenue": "operating_cash_flow",
        "revenue_year_over_year": "revenue",
    }
    for row in calculated["rows"]:
        period = AnnualPeriod.model_validate_json(_bytes(row["period"]))
        calculation_inputs = {normalized_ids[period, numerator_metrics[row["metric"]]]}
        denominator_period = row["prior_period"] if row["metric"] == "revenue_year_over_year" else row["period"]
        if denominator_period is not None:
            prior = AnnualPeriod.model_validate_json(_bytes(denominator_period))
            calculation_inputs.add(normalized_ids[prior, "revenue"])
        payload = {
            **row,
            "input_evidence_ids": sorted(calculation_inputs),
            "calculation_logic_version": calculated["calculation_logic_version"],
            "precision": calculated["precision"],
            "decimal_places": calculated["decimal_places"],
            "rounding_method": calculated["rounding_method"],
        }
        digest = _hash(payload)
        evidence = CalculatedEvidenceV1(
            evidence_id="calculated-" + digest,
            layer="calculated",
            input_evidence_ids=tuple(sorted(calculation_inputs)),
            value=row["value"],
            missing_reason=row["missing_reason"],
            unit=row["unit"],
            content_sha256=digest,
            data_version=digest,
            calculation_logic_version=calculated["calculation_logic_version"],
            rounding_method="ROUND_HALF_EVEN;precision=28;decimal_places=6",
        )
        records[evidence.evidence_id] = evidence
        payloads[evidence.evidence_id] = payload
    adapter: TypeAdapter[EvidenceRecordV1] = TypeAdapter(EvidenceRecordV1)
    for item in records.values():
        adapter.validate_json(item.model_dump_json())
        if isinstance(item, RawEvidenceV1):
            if not isinstance(records[item.source_metadata_evidence_id], SourceMetadataEvidenceV1):
                raise ValueError("annual_evidence_source_reference_invalid")
        elif isinstance(item, (NormalizedEvidenceV1, CalculatedEvidenceV1)):
            expected = RawEvidenceV1 if isinstance(item, NormalizedEvidenceV1) else NormalizedEvidenceV1
            if any(not isinstance(records.get(i), expected) for i in item.input_evidence_ids):
                raise ValueError("annual_evidence_input_layer_invalid")
    body = _bytes(
        {
            "version": 1,
            "kind": "internal-annual-p1-evidence-draft",
            "status": "pending",
            "analysis_ready": False,
            "task_id": task_id,
            "records": [r.model_dump(mode="json") for _, r in sorted(records.items())],
            "payloads": payloads,
            "artifact_locators": locators,
            "limitations": sorted(
                set(limitations)
                | {"annual_subset_only", "not_a_frozen_evidence_set", "not_transferred_during_preparation"}
            ),
        }
    )
    return body, {"sha256": sha256(body).hexdigest(), "record_counts": dict(Counter(r.layer for r in records.values()))}
