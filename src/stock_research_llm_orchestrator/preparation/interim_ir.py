"""Offline, value-private acceptance of eight pinned interim IR documents."""

import json
from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path
from typing import Literal

from pydantic import model_validator

from stock_research_llm_orchestrator.contracts.base import StrictContractModel
from stock_research_llm_orchestrator.preparation.fx_evidence import read_bundle
from stock_research_llm_orchestrator.preparation.market_revalidation import _safe_path, _timestamp
from stock_research_llm_orchestrator.preparation.price_fx_run import publish_run_preparation
from stock_research_llm_orchestrator.sources.toyota_ir_pdf import InterimDocument, extract_pages, parse_pages


POLICY_SHA256 = "57036694ffc1b0b49fc33a9bb99a54163fd6d81aea5a25557677a9555440aaad"


class InterimPolicy(StrictContractModel):
    """Exact issuer, periods and file hashes; no inferred rolling window."""

    version: Literal[1]
    security_code: Literal["7203"]
    edinet_code: Literal["E02144"]
    approval_reference: str
    documents: tuple[InterimDocument, ...]
    metadata_hashes: dict[str, str]
    limitations: tuple[str, ...]

    @model_validator(mode="after")
    def require_eight_periods(self) -> InterimPolicy:
        """Reject duplicate documents and repeated period endpoints."""
        if (
            len(self.documents) != 8
            or len({d.key for d in self.documents}) != 8
            or len({d.end_date for d in self.documents}) != 8
        ):
            raise ValueError("interim_period_scope_invalid")
        return self


def evaluate_interim(files: Mapping[str, bytes], policy_bytes: bytes) -> dict[str, bytes]:
    """Recompute values from exact PDFs, recording rejected metrics separately."""
    if sha256(policy_bytes).hexdigest() != POLICY_SHA256:
        raise ValueError("interim_policy_unapproved")
    policy = InterimPolicy.model_validate_json(policy_bytes)
    expected = set(policy.metadata_hashes)
    expected.update(f"{d.key}/{name}" for d in policy.documents for name in ("body.pdf", "receipt.json"))
    if set(files) != expected:
        raise ValueError("interim_source_inventory_invalid")
    for name, digest in policy.metadata_hashes.items():
        if sha256(files[name]).hexdigest() != digest:
            raise ValueError("interim_metadata_hash_mismatch")
    accepted_count = 0
    records = []
    coverage = []
    sources = []
    for document in policy.documents:
        body = files[f"{document.key}/body.pdf"]
        receipt = json.loads(files[f"{document.key}/receipt.json"])
        if (
            sha256(body).hexdigest() != document.sha256
            or receipt["sha256"] != document.sha256
            or receipt["url"] != document.url
            or receipt["bytes"] != len(body)
            or receipt["status"] != 200
            or _timestamp(receipt["retrieved_at"]).date() < document.published_on
        ):
            raise ValueError("interim_receipt_binding_invalid")
        metrics = parse_pages(extract_pages(body), document)
        records.append({"document": document.key, "metrics": [m.model_dump(mode="json") for m in metrics]})
        count = sum(m.status == "accepted" for m in metrics)
        accepted_count += count
        coverage.append(
            {
                "start_date": document.start_date.isoformat(),
                "end_date": document.end_date.isoformat(),
                "basis": "fiscal_year_to_date_and_period_end",
                "accepted_count": count,
                "status": "complete" if count == 6 else "partial" if count else "missing",
                "missing_metrics": [m.metric for m in metrics if m.status != "accepted"],
            }
        )
        sources.append(
            {
                **document.model_dump(mode="json"),
                "retrieved_at": receipt["retrieved_at"],
                "metrics": [m.model_dump(mode="json", exclude={"value"}) for m in metrics],
            }
        )
    result = {
        "policy.json": policy_bytes,
        "values.json": json.dumps(records, sort_keys=True, indent=2).encode(),
    }
    manifest = {
        "kind": "local-interim-ir-acceptance",
        "version": 1,
        "status": "pending",
        "analysis_ready": False,
        "security_code": policy.security_code,
        "edinet_code": policy.edinet_code,
        "accepted_count": accepted_count,
        "complete_interim_count": sum(c["status"] == "complete" for c in coverage),
        "interim_coverage": coverage,
        "sources": sources,
        "limitations": policy.limitations,
        "dependency_hashes": {name: sha256(body).hexdigest() for name, body in files.items()},
        "hashes": {name: sha256(body).hexdigest() for name, body in result.items()},
    }
    result["manifest.json"] = json.dumps(manifest, sort_keys=True, indent=2).encode()
    return result


def validate_interim(files: Mapping[str, bytes], source: Mapping[str, bytes]) -> None:
    """Compare the entire saved artifact to an independent local replay."""
    if dict(files) != evaluate_interim(source, files["policy.json"]):
        raise ValueError("interim_replay_mismatch")


def prepare_interim(source: Path, policy: Path, output: Path) -> None:
    """Publish an immutable local adoption without changing original documents."""
    source, policy, output = (_safe_path(p) for p in (source, policy, output))
    if any(output == p or output in p.parents or p in output.parents for p in (source, policy)):
        raise ValueError("interim_output_overlap")
    if output.exists():
        raise FileExistsError("interim_output_exists")
    inputs = read_bundle(source)
    result = evaluate_interim(inputs, policy.read_bytes())
    publish_run_preparation(output.parent, output.name, result, validator=lambda saved: validate_interim(saved, inputs))
