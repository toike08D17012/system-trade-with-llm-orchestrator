"""Bind selected narrative adoption to the canonical fixed disclosure acquisition."""

import json
from collections.abc import Mapping
from hashlib import sha256
from pathlib import Path

from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.task import DetailedAnalysisTaskV1
from stock_research_llm_orchestrator.preparation.fx_evidence import read_bundle
from stock_research_llm_orchestrator.preparation.interim_ir_runtime import verify_runtime
from stock_research_llm_orchestrator.preparation.market_revalidation import _timestamp
from stock_research_llm_orchestrator.requests.storage import ProductionRequestRepository
from stock_research_llm_orchestrator.sources.toyota_ir.disclosure_policy import KIND
from stock_research_llm_orchestrator.sources.toyota_ir_pdf import extract_pages


REVIEW_SHA256 = "031ce940c45f89287162af9d25a71f513b3f08525201e6486bf710a1578cbd7d"


def bind_disclosure_review(
    review_files: Mapping[str, bytes],
    acquisition_files: Mapping[str, bytes],
    repository: ProductionRequestRepository,
    raw_root: Path,
    task: DetailedAnalysisTaskV1,
    runtime: Path,
) -> dict[str, object]:
    """Replay provenance and extraction, without rerunning semantic judgement.

    The exact review pins selected assertions and unresolved references. It does
    not accept financial values, establish current share counts or prove coverage.
    """
    if set(review_files) != {"review.json"} or sha256(review_files["review.json"]).hexdigest() != REVIEW_SHA256:
        raise ValueError("disclosure_review_unapproved")
    review = json.loads(review_files["review.json"])
    acquisition = verify_runtime(acquisition_files, repository, raw_root, task, runtime)
    if acquisition.kind != KIND:
        raise ValueError("disclosure_review_acquisition_kind_invalid")
    if (
        review["task_id"] != task.task_id
        or review["security_code"] != task.security.security_code
        or review["acquisition_manifest_sha256"] != sha256(acquisition_files["manifest.json"]).hexdigest()
        or tuple(e["key"] for e in review["entries"]) != tuple(r.key for r in acquisition.receipts)
    ):
        raise ValueError("disclosure_review_acquisition_binding_invalid")
    documents = json.loads(acquisition_files["policy.json"])["documents"]
    reviewed_at = _timestamp(review["reviewed_at"])
    for entry, receipt, document in zip(review["entries"], acquisition.receipts, documents, strict=True):
        ref = receipt.raw_reference
        if entry["content_sha256"] != ref.content_sha256 or entry["published_on"] != document["published_on"]:
            raise ValueError("disclosure_review_document_binding_invalid")
        if _timestamp(receipt.received_at) > reviewed_at:
            raise ValueError("disclosure_review_before_acquisition")
        body = read_bundle(raw_root / ref.task_id / ref.relative_path)["body.bin"]
        if sha256(body).hexdigest() != ref.content_sha256:
            raise ValueError("disclosure_review_raw_changed")
        pages = extract_pages(body)
        extraction = json.dumps(pages, ensure_ascii=False, indent=2).encode()
        selected = entry["selected_pages"]
        if (
            sha256(extraction).hexdigest() != entry["extraction_sha256"]
            or not selected
            or len(set(selected)) != len(selected)
            or any(type(p) is not int or not 1 <= p <= len(pages) for p in selected)
        ):
            raise ValueError("disclosure_review_extraction_binding_invalid")
    return {
        "task_id": task.task_id,
        "review_sha256": REVIEW_SHA256,
        "review": review,
        "acquisition": acquisition.model_dump(mode="json"),
        "selected_document_count": len(acquisition.receipts),
        "semantic_review_reperformed": False,
        "analysis_ready": False,
    }
