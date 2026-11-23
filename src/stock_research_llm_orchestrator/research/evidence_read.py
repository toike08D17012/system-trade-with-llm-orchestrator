"""Expose retained documents through explicit, reproducible search and line selections."""

import json
from collections.abc import Mapping
from hashlib import sha256
from typing import cast

from pydantic import JsonValue

from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.acquisition_analysis import (
    AcquiredEvidence,
    EvidenceReadRequest,
)
from stock_research_llm_orchestrator.preparation.market_evidence import JPX_WORKBOOK_URL


def evidence_catalog(item: AcquiredEvidence) -> dict[str, JsonValue]:
    """Provide source identity and a document index, keeping the complete original in the snapshot."""
    value = cast(dict[str, JsonValue], item.model_dump(mode="json"))
    if item.source_id == "jpx" and item.source_reference == JPX_WORKBOOK_URL:
        return value
    value["text"] = json.dumps(
        {
            "kind": "retained_document_catalog",
            "line_count": len(item.text.splitlines()),
            "text_sha256": sha256(item.text.encode()).hexdigest(),
            "available_operations": ["search_evidence", "read_evidence"],
            "note": (
                "The complete extracted text and original are retained, not silently truncated. "
                "This catalog supplies no financial facts. "
                "Search for phrases, then request explicit inclusive line ranges, "
                "or independently read the original through native Web tools."
            ),
        },
        ensure_ascii=False,
    )
    return value


def read_evidence(
    request: EvidenceReadRequest, visible: Mapping[str, AcquiredEvidence], *, derived_text: bytes | None = None
) -> dict[str, JsonValue]:
    """Search all retained lines or return exactly the requested inclusive range, without network I/O."""
    result: dict[str, JsonValue] = {"request": request.model_dump(mode="json")}
    item = visible.get(request.evidence_id)
    if item is None:
        return {**result, "status": "not_available", "reason": "Evidence was not supplied to this role."}
    if item.source_id == "yfinance":
        return {
            **result,
            "status": "not_available",
            "reason": (
                "Market data is supplied as a deterministic per-security summary. "
                "This document reader does not transfer bulk raw price series; "
                "request focused market acquisition instead."
            ),
        }
    text = derived_text.decode() if derived_text is not None else item.text
    lines = text.splitlines()
    result.update(
        {
            "source_evidence_id": item.evidence_id,
            "source_reference": item.source_reference,
            "source_text_sha256": sha256(text.encode()).hexdigest(),
            "line_count": len(lines),
        }
    )
    if request.operation == "search_evidence":
        assert request.query is not None
        query = request.query.casefold()
        return {
            **result,
            "status": "searched",
            "matching_lines": [index for index, line in enumerate(lines, 1) if query in line.casefold()],
        }
    assert request.start_line is not None and request.end_line is not None
    if request.end_line > len(lines):
        return {**result, "status": "invalid_range", "reason": "Requested end_line exceeds the retained line_count."}
    return {
        **result,
        "status": "read",
        "selected_lines": [
            {"line": index, "text": lines[index - 1]} for index in range(request.start_line, request.end_line + 1)
        ],
    }
