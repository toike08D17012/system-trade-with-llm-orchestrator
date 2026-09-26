"""Retain XBRL candidates and unresolved filing relationships without normalization."""

from collections.abc import Sequence

from stock_research_llm_orchestrator.sources.edinet.document_list import EdinetDocument


def correction_reasons(document: EdinetDocument, documents: Sequence[EdinetDocument]) -> tuple[str, ...]:
    """Report ambiguous amendment graphs without choosing replacement financial facts."""
    reasons: set[str] = set()
    parent = document.parent_document_id
    amended = document.document_type.value in {"130", "150", "170"}
    if amended:
        reasons.add("amendment_selection_unresolved")
        if parent is None:
            reasons.add("amendment_parent_missing")
    seen = {document.document_id}
    while parent is not None:
        if parent in seen:
            reasons.add("amendment_cycle")
            break
        seen.add(parent)
        matches = [item for item in documents if item.document_id == parent]
        if not matches:
            reasons.add("amendment_parent_missing")
            break
        if any(item.edinet_code != document.edinet_code for item in matches):
            reasons.add("amendment_parent_issuer_mismatch")
            break
        if len({item.model_dump_json() for item in matches}) != 1:
            reasons.add("amendment_parent_snapshots_conflict")
            break
        parent = matches[0].parent_document_id
    if (
        document.parent_document_id
        and len({item.document_id for item in documents if item.parent_document_id == document.parent_document_id}) > 1
    ):
        reasons.add("competing_amendments")
    return tuple(sorted(reasons))
