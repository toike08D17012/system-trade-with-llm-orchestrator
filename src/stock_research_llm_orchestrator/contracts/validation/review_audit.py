"""Cross-artifact review and audit history validation."""

from collections.abc import Iterable

from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.dispute_audit import AuditResultV1


def validate_logical_audit_history(results: Iterable[AuditResultV1]) -> None:
    """Require one audit ID per dispute and one valid logical audit per dispute."""
    audit_targets: dict[str, str] = {}
    counted_disputes: set[str] = set()
    for result in results:
        existing_target = audit_targets.setdefault(result.audit_id, result.dispute_id)
        if existing_target != result.dispute_id:
            raise ValueError("one audit_id must not reference multiple dispute IDs")
        if not result.logical_audit_counted:
            continue
        if result.dispute_id in counted_disputes:
            raise ValueError("a dispute must have at most one valid logical audit")
        counted_disputes.add(result.dispute_id)
