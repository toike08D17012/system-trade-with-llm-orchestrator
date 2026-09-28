"""Reject forged or mismatched supplementary narrative references."""

import json
from hashlib import sha256
from io import BytesIO
from typing import Any
from zipfile import ZipFile

import pytest

from stock_research_llm_orchestrator.preparation import correction_review as module


def fixture_review(monkeypatch: pytest.MonkeyPatch) -> tuple[dict[str, bytes], dict[str, bytes], dict[str, bytes]]:
    """Build a synthetic pinned review so tests never depend on local runs."""
    member = "PublicDoc/review.htm"
    stream = BytesIO()
    with ZipFile(stream, "w") as archive:
        archive.writestr(member, b"omitted attachments")
    archive_bytes = stream.getvalue()
    review = {
        "original_document_id": "S100QZHY",
        "document_id": "S100RAR0",
        "archive_sha256": sha256(archive_bytes).hexdigest(),
        "member": member,
        "member_sha256": sha256(b"omitted attachments").hexdigest(),
        "reviewed_at": "2026-09-28T12:00:00Z",
        "review_mode": "coding_agent_public_source_review",
        "correction_kind": "omitted_attachments",
        "financial_restatement_described_in_selected_sections": False,
        "limitations": ["not_a_general_amendment_completeness_check"],
    }
    body = json.dumps(review).encode()
    monkeypatch.setattr(module, "REVIEW_SHA256", sha256(body).hexdigest())
    adoption = {
        "document_id": "S100RAR0",
        "archive_sha256": review["archive_sha256"],
        "matched_metric_period_count": 12,
        "retrieved_at": {"S100RAR0": "2026-09-27T12:00:00Z"},
    }
    return (
        {"narrative-review.json": body},
        {"raw/S100RAR0/body.bin": archive_bytes},
        {"manifest.json": json.dumps(adoption).encode()},
    )


def test_fixed_reference_retains_scope_and_task(monkeypatch: pytest.MonkeyPatch) -> None:
    """The supplement never implies a semantic replay or analysis readiness."""
    review, pair, adoption = fixture_review(monkeypatch)
    result = module.bind_correction_review(review, pair, adoption, "primary-task")
    assert result["task_id"] == "primary-task"
    assert result["semantic_review_reperformed"] is False
    assert result["analysis_ready"] is False
    assert result["limitations"] == ["not_a_general_amendment_completeness_check"]
    assert b"omitted attachments" not in json.dumps(result).encode()
    with pytest.raises(ValueError, match="unapproved"):
        module.bind_correction_review(
            {"narrative-review.json": review["narrative-review.json"] + b" "}, pair, adoption, "t"
        )
    with pytest.raises(ValueError, match="archive_mismatch"):
        module.bind_correction_review(review, {"raw/S100RAR0/body.bin": b"different"}, adoption, "t")


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [
        ("document_id", "OTHER", "adoption_mismatch"),
        ("archive_sha256", "0" * 64, "adoption_mismatch"),
        ("matched_metric_period_count", 6, "adoption_mismatch"),
        ("retrieved_at", {"S100RAR0": "2026-09-29T00:00:00Z"}, "before_acquisition"),
    ],
)
def test_adoption_binding_rejected(monkeypatch: pytest.MonkeyPatch, field: str, value: Any, error: str) -> None:
    """Valid source bytes cannot be attached to another adoption or earlier review."""
    review, pair, adoption = fixture_review(monkeypatch)
    manifest = json.loads(adoption["manifest.json"])
    manifest[field] = value
    with pytest.raises(ValueError, match=error):
        module.bind_correction_review(review, pair, {"manifest.json": json.dumps(manifest).encode()}, "t")


@pytest.mark.parametrize(
    ("field", "value", "error"),
    [("member", "absent.htm", "member_not_unique"), ("member_sha256", "0" * 64, "member_mismatch")],
)
def test_member_binding_rejected(monkeypatch: pytest.MonkeyPatch, field: str, value: str, error: str) -> None:
    """Even a pinned assertion must identify the exact retained member bytes."""
    review, pair, adoption = fixture_review(monkeypatch)
    assertion = json.loads(review["narrative-review.json"])
    assertion[field] = value
    body = json.dumps(assertion).encode()
    monkeypatch.setattr(module, "REVIEW_SHA256", sha256(body).hexdigest())
    with pytest.raises(ValueError, match=error):
        module.bind_correction_review({"narrative-review.json": body}, pair, adoption, "t")
