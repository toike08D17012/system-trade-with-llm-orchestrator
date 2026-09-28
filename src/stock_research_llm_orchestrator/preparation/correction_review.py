"""Bind the retained narrative review to the already validated amendment pair."""

import json
from collections.abc import Mapping
from hashlib import sha256
from io import BytesIO
from zipfile import BadZipFile, ZipFile

from stock_research_llm_orchestrator.preparation.edinet_pair import PAIR_IDS
from stock_research_llm_orchestrator.preparation.market_revalidation import _timestamp


REVIEW_SHA256 = "80d14b5fb119026da1f66a55f2f0c1b569fc5702258e8e90c1cab580f9c997a3"


def bind_correction_review(
    review_files: Mapping[str, bytes], pair: Mapping[str, bytes], adoption: Mapping[str, bytes], task_id: str
) -> dict[str, object]:
    """Verify a pinned assertion's provenance without repeating semantic review.

    Args:
        review_files: Retained review inventory containing narrative-review.json.
        pair: Amendment pair already replayed by financial pair acceptance.
        adoption: Already replayed pair adoption manifest.
        task_id: Primary run task receiving the supplementary reference.

    Returns:
        Metadata-only reference retaining the review's scope and limitations.

    Raises:
        ValueError: The fixed review or its document, member or time binding differs.
    """
    body = review_files["narrative-review.json"]
    if sha256(body).hexdigest() != REVIEW_SHA256:
        raise ValueError("correction_review_unapproved")
    review = json.loads(body)
    manifest = json.loads(adoption["manifest.json"])
    if (
        (review["original_document_id"], review["document_id"]) != PAIR_IDS
        or manifest["document_id"] != review["document_id"]
        or manifest["archive_sha256"] != review["archive_sha256"]
        or manifest["matched_metric_period_count"] != 12
    ):
        raise ValueError("correction_review_adoption_mismatch")
    archive = pair[f"raw/{review['document_id']}/body.bin"]
    if sha256(archive).hexdigest() != review["archive_sha256"]:
        raise ValueError("correction_review_archive_mismatch")
    try:
        with ZipFile(BytesIO(archive)) as source:
            members = [info for info in source.infolist() if info.filename == review["member"]]
            if len(members) != 1 or members[0].is_dir():
                raise ValueError("correction_review_member_not_unique")
            member = source.read(members[0])
    except (BadZipFile, RuntimeError, OSError) as error:
        raise ValueError("correction_review_archive_invalid") from error
    if sha256(member).hexdigest() != review["member_sha256"]:
        raise ValueError("correction_review_member_mismatch")
    reviewed_at = _timestamp(review["reviewed_at"])
    if any(_timestamp(t) > reviewed_at for t in manifest["retrieved_at"].values()):
        raise ValueError("correction_review_before_acquisition")
    return {
        "task_id": task_id,
        "review_sha256": REVIEW_SHA256,
        "document_id": review["document_id"],
        "original_document_id": review["original_document_id"],
        "archive_sha256": review["archive_sha256"],
        "member": review["member"],
        "member_sha256": review["member_sha256"],
        "reviewed_at": review["reviewed_at"],
        "review_mode": review["review_mode"],
        "correction_kind": review["correction_kind"],
        "financial_restatement_described_in_selected_sections": review[
            "financial_restatement_described_in_selected_sections"
        ],
        "limitations": review["limitations"],
        "semantic_review_reperformed": False,
        "analysis_ready": False,
    }
