"""Final machine-readable analysis and human-document reference contract."""

from typing import Literal

from pydantic import Field, field_validator, model_validator

from stock_research_llm_orchestrator.contracts.base import (
    ArtifactReferenceV1,
    FileReferenceV1,
    Identifier,
    NonEmptyString,
    StrictContractModel,
    Timestamp,
)
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.analysis import (
    CrossHorizonSummaryV1,
    EntryExitReferenceContextV1,
    SynthesizedHorizonResultV1,
)
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.evidence import _parse_rfc3339
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.task import (
    HumanDocumentLanguageOverrideV1,
    HumanDocumentType,
    SecurityIdentifierV1,
)


class TemplateProvenanceV1(StrictContractModel):
    """Exact trusted template and validated machine-readable input provenance."""

    template_id: Identifier
    template_version: int = Field(ge=1)
    template_reference: FileReferenceV1
    input_references: tuple[ArtifactReferenceV1, ...] = Field(strict=False, min_length=1)


class HumanDocumentValidationV1(StrictContractModel):
    """Required attestations before a human-facing Markdown artifact is adopted."""

    markdown_format_valid: Literal[True]
    configured_language_valid: Literal[True]
    foreign_quotes_have_configured_language_summary: Literal[True]
    assessments_match_machine_result: Literal[True]
    evaluability_matches_machine_result: Literal[True]
    confidence_matches_machine_result: Literal[True]
    missing_information_matches_machine_result: Literal[True]
    counterevidence_matches_machine_result: Literal[True]
    evidence_references_match_machine_result: Literal[True]
    new_fact_assessment_evidence_or_recommendation_added: Literal[False]
    secrets_or_disallowed_content_present: Literal[False]


class HumanDocumentArtifactV1(StrictContractModel):
    """Human-facing Markdown linked to exact machine-readable inputs."""

    document_type: Literal["final_report", "human_decision_request"]
    language: NonEmptyString
    markdown_reference: FileReferenceV1
    generated_at: Timestamp
    template_provenance: TemplateProvenanceV1
    validation: HumanDocumentValidationV1

    @field_validator("generated_at")
    @classmethod
    def ensure_valid_generated_at(cls, value: str) -> str:
        """Validate the document generation timestamp."""
        _parse_rfc3339(value, "generated_at")
        return value


class AuditInvocationRecordV1(StrictContractModel):
    """Why one material dispute was or was not sent to conditional audit."""

    dispute_id: Identifier
    decision: Literal["start_audit", "do_not_audit", "human_decision_required"]
    reason: NonEmptyString
    audit_request_reference: ArtifactReferenceV1 | None
    audit_result_reference: ArtifactReferenceV1 | None

    @model_validator(mode="after")
    def validate_audit_references(self) -> AuditInvocationRecordV1:
        """Require request/result references only when the audit was started."""
        started = self.decision == "start_audit"
        if started and self.audit_request_reference is None:
            raise ValueError("started audits require an audit-request reference")
        if not started and (self.audit_request_reference is not None or self.audit_result_reference is not None):
            raise ValueError("non-started audits must not claim request or result references")
        if (
            self.audit_request_reference is not None
            and self.audit_request_reference.schema_id != "detailed-analysis.audit-request"
        ):
            raise ValueError("audit request reference must use the audit-request schema")
        if (
            self.audit_result_reference is not None
            and self.audit_result_reference.schema_id != "detailed-analysis.audit-result"
        ):
            raise ValueError("audit result reference must use the audit-result schema")
        return self


class FinalAnalysisResultV1(StrictContractModel):
    """English machine-readable source of truth for an analysis-completed task."""

    schema_id: Literal["detailed-analysis.final-analysis-result"]
    schema_version: Literal[1]
    task_id: Identifier
    artifact_id: Identifier
    created_at: Timestamp
    security: SecurityIdentifierV1
    task_reference: ArtifactReferenceV1
    evidence_set_reference: ArtifactReferenceV1
    evidence_set_version: int = Field(ge=1)
    evidence_frozen_at: Timestamp
    freshness_checked_at: Timestamp
    freshness_search_scope: tuple[NonEmptyString, ...] = Field(strict=False, min_length=1)
    freshness_search_result: NonEmptyString
    synthesis_reference: ArtifactReferenceV1
    primary_review_reference: ArtifactReferenceV1
    horizon_results: tuple[SynthesizedHorizonResultV1, ...] = Field(strict=False, min_length=2, max_length=2)
    cross_horizon_summary: CrossHorizonSummaryV1
    entry_exit_context: EntryExitReferenceContextV1
    unresolved_difference_ids: tuple[Identifier, ...] = Field(strict=False)
    missing_information: tuple[NonEmptyString, ...] = Field(strict=False)
    audit_invocations: tuple[AuditInvocationRecordV1, ...] = Field(strict=False)
    human_decision_references: tuple[ArtifactReferenceV1, ...] = Field(strict=False)
    human_document_language_override: HumanDocumentLanguageOverrideV1 | None
    human_documents: tuple[HumanDocumentArtifactV1, ...] = Field(strict=False, min_length=1)
    log_references: tuple[FileReferenceV1, ...] = Field(strict=False, min_length=1)
    machine_readable_natural_language: Literal["en"]
    investment_advice: Literal[False]
    purchase_recommendation: Literal[False]
    order_instruction: Literal[False]
    return_guarantee: Literal[False]
    human_final_investment_decision_required: Literal[True]

    @field_validator("created_at", "evidence_frozen_at", "freshness_checked_at")
    @classmethod
    def ensure_valid_timestamps(cls, value: str, info: object) -> str:
        """Validate result and evidence timestamps."""
        _parse_rfc3339(value, getattr(info, "field_name", "timestamp"))
        return value

    @model_validator(mode="after")
    def validate_final_result(self) -> FinalAnalysisResultV1:
        """Bind exact inputs and preserve separate medium- and long-term results."""
        expected_schemas = (
            (self.task_reference, "detailed-analysis.detailed-analysis-task"),
            (self.evidence_set_reference, "detailed-analysis.evidence-set"),
            (self.synthesis_reference, "detailed-analysis.synthesis-result"),
            (self.primary_review_reference, "detailed-analysis.primary-review"),
        )
        if any(reference.schema_id != expected for reference, expected in expected_schemas):
            raise ValueError("final analysis inputs must reference their exact public contract schemas")
        if tuple(result.horizon for result in self.horizon_results) != ("medium_term", "long_term"):
            raise ValueError("final analysis requires medium_term followed by long_term")
        frozen_at = _parse_rfc3339(self.evidence_frozen_at, "evidence_frozen_at")
        freshness_at = _parse_rfc3339(self.freshness_checked_at, "freshness_checked_at")
        created_at = _parse_rfc3339(self.created_at, "created_at")
        if freshness_at < frozen_at or created_at < freshness_at:
            raise ValueError("final result timestamps must follow the evidence lifecycle order")
        if any(
            reference.schema_id != "detailed-analysis.human-decision" for reference in self.human_decision_references
        ):
            raise ValueError("human decisions must use the human-decision schema")
        final_reports = [document for document in self.human_documents if document.document_type == "final_report"]
        if len(final_reports) != 1:
            raise ValueError("final analysis requires exactly one human-facing final report")
        language_override = self.human_document_language_override
        if language_override is not None and HumanDocumentType.FINAL_REPORT in language_override.target_documents:
            expected_language = language_override.language
        else:
            expected_language = "ja"
        if final_reports[0].language != expected_language:
            raise ValueError("the final report language must match the task override or Japanese default")
        return self
