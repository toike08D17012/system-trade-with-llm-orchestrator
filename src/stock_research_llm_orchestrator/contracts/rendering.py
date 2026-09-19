"""Pure reference rendering and exact Markdown consistency validation."""

import hashlib
from collections.abc import Mapping
from typing import Literal, cast

from jinja2 import StrictUndefined, meta, nodes
from jinja2.sandbox import SandboxedEnvironment
from pydantic import Field, model_validator

from stock_research_llm_orchestrator.contracts.base import NonEmptyString, StrictContractModel
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.dispute_audit import (
    AuditResultV1,
    DisputeV1,
)
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.final_output import FinalAnalysisResultV1
from stock_research_llm_orchestrator.contracts.errors import ErrorCategory, ErrorCode
from stock_research_llm_orchestrator.contracts.validation.issues import RuleValidationError, ValidationIssue


HumanDocumentLanguage = Literal["ja", "en"]

_FORBIDDEN_TEMPLATE_NODES = (nodes.Extends, nodes.Include, nodes.Import, nodes.FromImport)
_FINAL_REPORT_VARIABLES = frozenset({"document", "language"})
_HUMAN_DECISION_VARIABLES = frozenset({"request", "language"})
_FINAL_REPORT_SECTIONS = {
    "ja": ("# 個別株 詳細解析レポート", "## 中期評価", "## 長期評価", "## 安全上の注意"),
    "en": (
        "# Equity Detailed Analysis Report",
        "## Medium-term assessment",
        "## Long-term assessment",
        "## Safety notice",
    ),
}
_HUMAN_DECISION_SECTIONS = {
    "ja": ("# 人間判断依頼", "## 未解決の問い", "## 許可された選択肢", "## 安全上の注意"),
    "en": ("# Human Decision Request", "## Unresolved question", "## Allowed options", "## Safety notice"),
}


class HumanDecisionRequestInputV1(StrictContractModel):
    """Validated, non-persisted input for one human workflow decision request."""

    dispute: DisputeV1
    audit_result: AuditResultV1 | None
    allowed_options: tuple[NonEmptyString, ...] = Field(strict=False, min_length=2)
    language: HumanDocumentLanguage

    @model_validator(mode="after")
    def validate_request(self) -> HumanDecisionRequestInputV1:
        """Bind an optional audit result and require unique workflow options."""
        if len(self.allowed_options) != len(set(self.allowed_options)):
            raise ValueError("human decision request options must be unique")
        if self.audit_result is not None:
            if self.audit_result.task_id != self.dispute.task_id:
                raise ValueError("audit result and dispute must belong to the same task")
            if self.audit_result.dispute_id != self.dispute.dispute_id:
                raise ValueError("audit result must refer to the rendered dispute")
        return self


def render_final_report(result: FinalAnalysisResultV1, template_source: str) -> str:
    """Render a final report without filesystem, network, clock, or random access."""
    final_report = next(document for document in result.human_documents if document.document_type == "final_report")
    language = _validated_language(final_report.language)
    rendered = _render_template(
        template_source,
        {"document": result.model_dump(mode="json"), "language": language},
        allowed_variables=_FINAL_REPORT_VARIABLES,
    )
    _ensure_required_sections(rendered, _FINAL_REPORT_SECTIONS[language])
    return rendered


def render_human_decision_request(request: HumanDecisionRequestInputV1, template_source: str) -> str:
    """Render one dispute-scoped human decision request from validated inputs."""
    rendered = _render_template(
        template_source,
        {"request": request.model_dump(mode="json"), "language": request.language},
        allowed_variables=_HUMAN_DECISION_VARIABLES,
    )
    _ensure_required_sections(rendered, _HUMAN_DECISION_SECTIONS[request.language])
    return rendered


def validate_final_report_markdown(
    result: FinalAnalysisResultV1,
    template_source: str,
    markdown: str,
) -> None:
    """Require byte-identical output from the exact final result and template."""
    _ensure_exact_markdown(render_final_report(result, template_source), markdown)


def validate_human_decision_request_markdown(
    request: HumanDecisionRequestInputV1,
    template_source: str,
    markdown: str,
) -> None:
    """Require byte-identical output from the exact decision input and template."""
    _ensure_exact_markdown(render_human_decision_request(request, template_source), markdown)


def content_sha256(content: str) -> str:
    """Return the lowercase SHA-256 digest of exact UTF-8 content."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _render_template(
    template_source: str,
    context: Mapping[str, object],
    *,
    allowed_variables: frozenset[str],
) -> str:
    if "\r" in template_source:
        raise ValueError("Markdown templates must use LF newlines")
    environment = SandboxedEnvironment(
        autoescape=False,
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        newline_sequence="\n",
    )
    environment.globals.clear()
    parsed = environment.parse(template_source)
    if any(parsed.find_all(_FORBIDDEN_TEMPLATE_NODES)):
        raise ValueError("Markdown templates must not load or extend other templates")
    undeclared = meta.find_undeclared_variables(parsed)
    if undeclared - allowed_variables:
        raise ValueError("Markdown template references fields outside the approved context")
    rendered = environment.from_string(template_source).render(context)
    if "\r" in rendered or not rendered.endswith("\n"):
        raise ValueError("rendered Markdown must use LF newlines and end with one newline")
    return rendered


def _validated_language(value: str) -> HumanDocumentLanguage:
    if value == "ja" or value == "en":
        return cast("HumanDocumentLanguage", value)
    raise ValueError("the version 1 reference templates support only ja and en")


def _ensure_required_sections(markdown: str, sections: tuple[str, ...]) -> None:
    if any(section not in markdown for section in sections):
        raise ValueError("rendered Markdown is missing a required section")


def _ensure_exact_markdown(expected: str, actual: str) -> None:
    if actual != expected:
        raise RuleValidationError(
            ValidationIssue(
                category=ErrorCategory.SEMANTIC,
                code=ErrorCode.MARKDOWN_MISMATCH,
                message="Markdown does not match the exact validated input and template",
                instance_path="/human_documents",
                context={
                    "expected_sha256": content_sha256(expected),
                    "actual_sha256": content_sha256(actual),
                },
            )
        )
