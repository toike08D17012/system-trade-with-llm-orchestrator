"""Tests for deterministic human-facing Markdown rendering."""

import json
import typing
from pathlib import Path

import pytest

from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.dispute_audit import (
    AuditResultV1,
    DisputeV1,
)
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.final_output import FinalAnalysisResultV1
from stock_research_llm_orchestrator.contracts.rendering import (
    HumanDecisionRequestInputV1,
    content_sha256,
    render_final_report,
    render_human_decision_request,
    validate_final_report_markdown,
    validate_human_decision_request_markdown,
)
from stock_research_llm_orchestrator.contracts.validation.issues import RuleValidationError


REPOSITORY_ROOT = Path(__file__).parents[2]
FIXTURE_ROOT = REPOSITORY_ROOT / "tests" / "fixtures" / "contracts" / "detailed-analysis" / "v1"
TEMPLATE_ROOT = REPOSITORY_ROOT / "templates" / "detailed-analysis" / "v1"


def _artifact(artifact: str, filename: str) -> dict[str, typing.Any]:
    path = FIXTURE_ROOT / artifact / "valid" / filename
    parsed = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict)
    assert all(isinstance(key, str) for key in parsed)
    return typing.cast("dict[str, typing.Any]", parsed)


def _template(filename: str) -> str:
    return (TEMPLATE_ROOT / filename).read_text(encoding="utf-8")


def test_final_report_is_byte_reproducible() -> None:
    """Render the same validated result and template to identical UTF-8 content."""
    result = FinalAnalysisResultV1.model_validate(_artifact("final-analysis-result", "completed.json"))
    template = _template("final-report.md.j2")

    first = render_final_report(result, template)
    second = render_final_report(result, template)

    assert first == second
    assert content_sha256(first) == content_sha256(second)
    assert "# 個別株 詳細解析レポート" in first
    assert "`conditional`" in first
    assert "`normalized-1`" in first


def test_final_result_tracks_the_exact_template_source() -> None:
    """Keep the final result provenance bound to the versioned repository template."""
    result = FinalAnalysisResultV1.model_validate(_artifact("final-analysis-result", "completed.json"))
    final_report = next(document for document in result.human_documents if document.document_type == "final_report")
    template = _template("final-report.md.j2")

    assert final_report.template_provenance.template_version == 1
    assert final_report.template_provenance.template_reference.sha256 == content_sha256(template)


def test_final_report_detects_any_markdown_change() -> None:
    """Reject a human report with even one untracked projection change."""
    result = FinalAnalysisResultV1.model_validate(_artifact("final-analysis-result", "completed.json"))
    template = _template("final-report.md.j2")
    rendered = render_final_report(result, template)

    with pytest.raises(RuleValidationError, match="does not match") as captured:
        validate_final_report_markdown(result, template, rendered.replace("conditional", "pass", 1))

    assert captured.value.issue.context["expected_sha256"] != captured.value.issue.context["actual_sha256"]


def test_final_report_honors_explicit_english_override() -> None:
    """Select the English branch only from the recorded task-level override."""
    payload = _artifact("final-analysis-result", "completed.json")
    payload["human_document_language_override"] = {
        "target_documents": ["final_report"],
        "language": "en",
        "reason": "The human reviewer requested English.",
    }
    payload["human_documents"][0]["language"] = "en"  # type: ignore[index]
    result = FinalAnalysisResultV1.model_validate(payload)

    rendered = render_final_report(result, _template("final-report.md.j2"))

    assert rendered.startswith("# Equity Detailed Analysis Report\n")


def test_template_rejects_unapproved_context_variables() -> None:
    """Do not expose ambient values to a repository template."""
    result = FinalAnalysisResultV1.model_validate(_artifact("final-analysis-result", "completed.json"))

    with pytest.raises(ValueError, match="outside the approved context"):
        render_final_report(result, "# {{ secret_value }}\n")


def test_human_decision_request_is_reproducible_and_exact() -> None:
    """Render one dispute and optional audit without changing the allowed options."""
    request = HumanDecisionRequestInputV1(
        dispute=DisputeV1.model_validate(_artifact("dispute", "material-policy-difference.json")),
        audit_result=AuditResultV1.model_validate(_artifact("audit-result", "support-first.json")),
        allowed_options=("apply_interpretation_1", "stop_with_unresolved_status"),
        language="ja",
    )
    template = _template("human-decision-request.md.j2")

    rendered = render_human_decision_request(request, template)

    assert rendered == render_human_decision_request(request, template)
    assert "`apply_interpretation_1`" in rendered
    validate_human_decision_request_markdown(request, template, rendered)


def test_human_decision_request_rejects_cross_dispute_audit() -> None:
    """Prevent an audit opinion from leaking into an unrelated decision request."""
    audit_payload = _artifact("audit-result", "support-first.json")
    audit_payload["dispute_id"] = "dispute-2"

    with pytest.raises(ValueError, match="rendered dispute"):
        HumanDecisionRequestInputV1(
            dispute=DisputeV1.model_validate(_artifact("dispute", "material-policy-difference.json")),
            audit_result=AuditResultV1.model_validate(audit_payload),
            allowed_options=("continue", "stop"),
            language="ja",
        )
