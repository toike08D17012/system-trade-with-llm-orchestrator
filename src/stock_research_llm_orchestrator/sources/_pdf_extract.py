"""Minimal subprocess entrypoint for bounded PDF text extraction."""

import io
import json
import sys


MAX_PDF_BYTES = 5_000_000
MAX_PAGES = 64
MAX_TEXT_BYTES = 2_000_000


def _extract_worker() -> None:
    """Run PDF decompression in a process with CPU, memory and output limits."""
    import logging
    import resource

    from pypdf import PdfReader

    resource.setrlimit(resource.RLIMIT_CPU, (20, 20))
    resource.setrlimit(resource.RLIMIT_AS, (768_000_000, 768_000_000))
    logging.disable(logging.CRITICAL)
    body = sys.stdin.buffer.read(MAX_PDF_BYTES + 1)
    if not body.startswith(b"%PDF-") or len(body) > MAX_PDF_BYTES:
        raise ValueError("invalid_pdf")
    reader = PdfReader(io.BytesIO(body), strict=True)
    if reader.is_encrypted and not reader.decrypt(""):
        raise ValueError("pdf_password_required")
    if not 1 <= len(reader.pages) <= MAX_PAGES:
        raise ValueError("unsupported_pdf")
    texts = []
    expanded = 0
    for page in reader.pages:
        contents = page.get_contents()
        expanded += len(contents.get_data()) if contents is not None else 0
        if expanded > 20_000_000:
            raise ValueError("pdf_content_limit")
        texts.append(page.extract_text(extraction_mode="layout"))
        if sum(len(t.encode()) for t in texts) > MAX_TEXT_BYTES:
            raise ValueError("pdf_text_limit")
    if not any(t.strip() for t in texts):
        raise ValueError("pdf_text_missing")
    sys.stdout.write(json.dumps(texts))


if __name__ == "__main__":
    try:
        if sys.argv[1:] != ["--extract"]:
            raise ValueError("unsupported_mode")
        _extract_worker()
    except Exception as error:
        sys.stderr.write(type(error).__name__)
        raise SystemExit(1) from None
