"""Cross-artifact consistency validation for an adopted final result."""

from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.analysis import SynthesisResultV1
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.evidence import EvidenceSetV1
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.final_output import FinalAnalysisResultV1
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.review import PrimaryReviewV1
from stock_research_llm_orchestrator.contracts.errors import ErrorCategory, ErrorCode
from stock_research_llm_orchestrator.contracts.validation.issues import RuleValidationError, ValidationIssue


def validate_final_result_consistency(
    final_result: FinalAnalysisResultV1,
    synthesis: SynthesisResultV1,
    primary_review: PrimaryReviewV1,
    evidence_set: EvidenceSetV1,
) -> None:
    """Validate the exact task, artifact, evidence, and semantic projection links."""
    task_ids = {final_result.task_id, synthesis.task_id, primary_review.task_id, evidence_set.task_id}
    if len(task_ids) != 1:
        _raise_conflict("final result inputs must belong to one task", "/task_id")
    expected_references = (
        (final_result.synthesis_reference.artifact_id, synthesis.artifact_id, "/synthesis_reference"),
        (
            final_result.primary_review_reference.artifact_id,
            primary_review.artifact_id,
            "/primary_review_reference",
        ),
        (
            final_result.evidence_set_reference.artifact_id,
            evidence_set.artifact_id,
            "/evidence_set_reference",
        ),
        (
            primary_review.reviewed_synthesis_reference.artifact_id,
            synthesis.artifact_id,
            "/primary_review_reference/reviewed_synthesis_reference",
        ),
    )
    for referenced_id, actual_id, instance_path in expected_references:
        if referenced_id != actual_id:
            _raise_missing_reference(instance_path, referenced_id)
    if (
        len(
            {
                final_result.evidence_set_version,
                synthesis.evidence_set_version,
                primary_review.evidence_set_version,
                evidence_set.evidence_set_version,
            }
        )
        != 1
    ):
        _raise_conflict("final result inputs must use one evidence-set version", "/evidence_set_version")
    semantic_pairs = (
        (final_result.horizon_results, synthesis.horizon_results, "/horizon_results"),
        (final_result.cross_horizon_summary, synthesis.cross_horizon_summary, "/cross_horizon_summary"),
        (final_result.entry_exit_context, synthesis.entry_exit_context, "/entry_exit_context"),
    )
    for final_value, source_value, instance_path in semantic_pairs:
        if final_value != source_value:
            _raise_conflict("final result must preserve the reviewed synthesis value", instance_path)
    difference_ids = {difference.difference_id for difference in synthesis.differences}
    missing_differences = set(final_result.unresolved_difference_ids) - difference_ids
    if missing_differences:
        _raise_missing_reference("/unresolved_difference_ids", sorted(missing_differences)[0])
    evidence_ids = {record.evidence_id for record in evidence_set.records}
    missing_evidence = set(final_result.entry_exit_context.evidence_ids) - evidence_ids
    if missing_evidence:
        _raise_missing_reference("/entry_exit_context/evidence_ids", sorted(missing_evidence)[0])
    if not primary_review.review_passed:
        _raise_conflict("final result requires a passing primary review", "/primary_review_reference")


def _raise_conflict(message: str, instance_path: str) -> None:
    raise RuleValidationError(
        ValidationIssue(
            category=ErrorCategory.SEMANTIC,
            code=ErrorCode.ARTIFACT_CONFLICT,
            message=message,
            instance_path=instance_path,
        )
    )


def _raise_missing_reference(instance_path: str, reference_id: str) -> None:
    raise RuleValidationError(
        ValidationIssue(
            category=ErrorCategory.REFERENCE,
            code=ErrorCode.REFERENCE_NOT_FOUND,
            message="final result reference does not resolve to the supplied artifact",
            instance_path=instance_path,
            context={"reference_id": reference_id},
        )
    )
