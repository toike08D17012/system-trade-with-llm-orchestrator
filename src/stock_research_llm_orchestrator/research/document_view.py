"""Retain exact source text and deterministic, explicitly derived reader views."""

import io
import json
import re
from hashlib import sha256
from typing import cast

from pydantic import JsonValue
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.acquisition_analysis import AcquiredEvidence


_VIEW_LIMITATIONS = (
    "Derived reader view; canonical source.txt and its line numbers remain authoritative. "
    "Formatting does not reconstruct original headings, tables, charts, or visual reading order. "
    "No OCR is performed; image-only content is not recovered."
)


def _pdf_page_map(item: AcquiredEvidence, raw: bytes | None) -> dict[str, JsonValue]:
    """Map physical pages only when extraction reproduces the complete canonical text."""
    metadata: dict[str, JsonValue] = {
        "kind": "retained-document-page-map",
        "format_version": 1,
        "text_sha256": sha256(item.text.encode()).hexdigest(),
        "line_count": len(item.text.splitlines()),
        "status": "unavailable",
        "pages": [],
        "limitations": [],
    }
    if raw is None:
        metadata["limitations"] = ["Original PDF bytes are unavailable; physical page spans cannot be verified."]
        return metadata
    try:
        reader = PdfReader(io.BytesIO(raw), strict=True)
        if reader.is_encrypted and not reader.decrypt(""):
            raise ValueError("document_view_pdf_encrypted")
        page_lines = [
            [line.strip() for line in page.extract_text(extraction_mode="layout").splitlines() if line.strip()]
            for page in reader.pages
        ]
    except OSError, ValueError, TypeError, KeyError, PdfReadError:
        metadata["limitations"] = ["Original PDF text could not be extracted; physical page spans are unavailable."]
        return metadata
    if "\n".join(line for lines in page_lines for line in lines) != item.text:
        metadata["status"] = "mismatch"
        metadata["limitations"] = [
            "PDF page extraction does not exactly match canonical source text; no page spans are asserted."
        ]
        return metadata
    pages: list[JsonValue] = []
    limitations: list[JsonValue] = []
    next_line = 1
    for page_number, lines in enumerate(page_lines, 1):
        pages.append(
            {
                "physical_page": page_number,
                "start_line": next_line if lines else None,
                "end_line": next_line + len(lines) - 1 if lines else None,
            }
        )
        if not lines:
            limitations.append(
                f"Physical PDF page {page_number} has no extractable text; no canonical line span or OCR is available."
            )
        next_line += len(lines)
    metadata.update({"status": "mapped", "pages": pages, "limitations": limitations})
    return metadata


def document_view_files(
    key: str, item: AcquiredEvidence, raw: bytes | None, media_type: str | None
) -> dict[str, bytes]:
    """Build content-only views without changing canonical text or inventing source structure.

    Args:
        key: Caller-supplied content key for the retained summary directory.
        item: Evidence containing the immutable canonical extracted text.
        raw: Original bytes, when available for physical PDF page verification.
        media_type: Original media type, or None when it is unknown.

    Returns:
        Exact text, a line-numbered Markdown view, and PDF page-map metadata when applicable.
    """
    prefix = f"summaries/{key}"
    files = {f"{prefix}/source.txt": item.text.encode()}
    limitations = [_VIEW_LIMITATIONS]
    if media_type == "application/pdf":
        page_map = _pdf_page_map(item, raw)
        files[f"{prefix}/page-map.json"] = (json.dumps(page_map, ensure_ascii=False, indent=2) + "\n").encode()
        limitations.append(f"Physical PDF page mapping status: {page_map['status']}.")
        limitations.extend(str(message) for message in cast(list[JsonValue], page_map["limitations"]))
    else:
        limitations.append("Physical page and source-section mappings are unavailable; none have been inferred.")
    longest_fence = max((len(match.group()) for match in re.finditer(r"`+", item.text)), default=0)
    fence = "`" * max(3, longest_fence + 1)
    lines = [
        "# Derived source reader view",
        "",
        *limitations,
        "",
        "Each prefix below is a 1-based canonical source line number, not part of the original text.",
        "",
        fence + "text",
        *(f"{number}: {line}" for number, line in enumerate(item.text.splitlines(), 1)),
        fence,
        "",
    ]
    files[f"{prefix}/source.md"] = "\n".join(lines).encode()
    return files
