"""Turn a locally acquired public document into the next research-draft input."""

import argparse
import io
import json
from collections.abc import Mapping
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path
from typing import Literal

from pydantic import Field
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from stock_research_llm_orchestrator.contracts.base import StrictContractModel
from stock_research_llm_orchestrator.preparation.research_draft import (
    DraftEvidence,
    DraftInput,
    DraftOutput,
    validate_bundle,
)
from stock_research_llm_orchestrator.preparation.storage import publish_preparation, read_bundle


class FollowUpIntake(StrictContractModel):
    """Operator-verified acquisition associated with one model request."""

    request_index: int = Field(ge=0)
    source_id: str = Field(pattern=r"^[a-z0-9-]+$")
    approval_version: int = Field(ge=1)
    source_url: str
    retrieved_at: str
    media_type: Literal["text/plain", "text/html", "application/pdf"]


class _HtmlText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag in {"script", "style", "noscript"}:
            self.hidden += 1
        elif tag in {"p", "div", "li", "tr", "h1", "h2", "h3", "h4", "br"}:
            self.parts.append("\n")
        elif tag in {"td", "th"}:
            self.parts.append(" | ")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"}:
            self.hidden -= 1
        elif tag in {"p", "li", "tr", "h1", "h2", "h3", "h4"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def extract_text(body: bytes, media_type: str) -> str:
    """Extract searchable text without issuer-specific document assumptions."""
    if media_type == "application/pdf":
        try:
            reader = PdfReader(io.BytesIO(body), strict=True)
            if reader.is_encrypted:
                raise ValueError("follow_up_pdf_encrypted")
            text = "\n\n".join(page.extract_text(extraction_mode="layout") for page in reader.pages)
        except OSError, ValueError, TypeError, KeyError, PdfReadError:
            raise ValueError("follow_up_pdf_invalid") from None
    elif media_type in {"text/plain", "text/html"}:
        try:
            decoded = body.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise ValueError("follow_up_encoding_invalid") from None
        if media_type == "text/html":
            parser = _HtmlText()
            parser.feed(decoded)
            text = "".join(parser.parts)
        else:
            text = decoded
    else:
        raise ValueError("follow_up_media_type_invalid")
    cleaned = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    if not cleaned:
        raise ValueError("follow_up_text_empty")
    return cleaned


def _chunks(text: str, size: int = 20_000) -> tuple[str, ...]:
    return tuple(text[index : index + size] for index in range(0, len(text), size))


def _assemble(prior: Mapping[str, bytes], intake: FollowUpIntake, body: bytes) -> dict[str, bytes]:
    """Bind the chosen request and source bytes to one complete next-pass input."""
    validate_bundle(prior)
    previous_input = DraftInput.model_validate_json(prior["input.json"])
    previous_output = DraftOutput.model_validate_json(prior["draft.json"])
    if intake.request_index >= len(previous_output.follow_up_requests):
        raise ValueError("follow_up_request_missing")
    text = extract_text(body, intake.media_type)
    digest = sha256(body).hexdigest()
    additions = tuple(
        DraftEvidence(
            evidence_id=f"follow-up-{digest[:16]}-{index + 1}",
            source_id=intake.source_id,
            approval_version=intake.approval_version,
            source_url=intake.source_url,
            source_sha256=digest,
            retrieved_at=intake.retrieved_at,
            text=chunk,
        )
        for index, chunk in enumerate(_chunks(text))
    )
    next_input = DraftInput.model_validate(
        previous_input.model_dump() | {"evidence": (*previous_input.evidence, *additions)}
    )
    input_bytes = next_input.model_dump_json(indent=2).encode() + b"\n"
    intake_bytes = intake.model_dump_json(indent=2).encode() + b"\n"
    manifest = {
        "kind": "research-follow-up-input",
        "request_index": intake.request_index,
        "request": previous_output.follow_up_requests[intake.request_index].model_dump(),
        "prior_input_sha256": sha256(prior["input.json"]).hexdigest(),
        "prior_draft_sha256": sha256(prior["draft.json"]).hexdigest(),
        "source_sha256": digest,
        "extracted_text_sha256": sha256(text.encode()).hexdigest(),
        "input_sha256": sha256(input_bytes).hexdigest(),
    }
    files = {
        "input.json": input_bytes,
        "intake.json": intake_bytes,
        "source.bin": body,
        "prior-input.json": prior["input.json"],
        "prior-draft.json": prior["draft.json"],
        "prior-manifest.json": prior["manifest.json"],
        "manifest.json": json.dumps(manifest, indent=2).encode() + b"\n",
    }
    return files


def build_follow_up(prior: Mapping[str, bytes], intake: FollowUpIntake, body: bytes) -> dict[str, bytes]:
    """Build and verify the next-pass input before publication."""
    files = _assemble(prior, intake, body)
    validate_follow_up(files)
    return files


def validate_follow_up(files: Mapping[str, bytes]) -> None:
    """Recompute the exact follow-up input from retained bytes."""
    if set(files) != {
        "input.json",
        "intake.json",
        "source.bin",
        "prior-input.json",
        "prior-draft.json",
        "prior-manifest.json",
        "manifest.json",
    }:
        raise ValueError("follow_up_inventory_invalid")
    prior = {
        "input.json": files["prior-input.json"],
        "draft.json": files["prior-draft.json"],
        "manifest.json": files["prior-manifest.json"],
    }
    expected = _assemble(prior, FollowUpIntake.model_validate_json(files["intake.json"]), files["source.bin"])
    if dict(files) != expected:
        raise ValueError("follow_up_binding_invalid")


def _safe_document(path: Path) -> bytes:
    absolute = path.absolute()
    if ".." in path.parts or any(part.is_symlink() for part in (absolute, *absolute.parents)) or not absolute.is_file():
        raise ValueError("follow_up_document_path_invalid")
    return absolute.read_bytes()


def main(argv: list[str] | None = None) -> int:
    """Prepare a next-pass input or replay one saved follow-up bundle."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--prior", type=Path, required=True)
    prepare.add_argument("--intake", type=Path, required=True)
    prepare.add_argument("--document", type=Path, required=True)
    prepare.add_argument("--runs", type=Path, required=True)
    prepare.add_argument("--destination", required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("--bundle", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            files = build_follow_up(
                read_bundle(args.prior),
                FollowUpIntake.model_validate_json(args.intake.read_bytes()),
                _safe_document(args.document),
            )
            publish_preparation(args.runs, args.destination, files, validator=validate_follow_up)
            bundle = args.runs / args.destination
        else:
            bundle = args.bundle
        validate_follow_up(read_bundle(bundle))
        print(f"research_follow_up: valid; next_input={bundle / 'input.json'}")
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(f"research_follow_up_failed: {type(error).__name__}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
