"""Advance one provisional research pass using a bounded public IR follow-up."""

import argparse
import re
from pathlib import Path
from urllib.parse import urlsplit

from stock_research_llm_orchestrator.preparation.public_ir_follow_up import acquire_public_ir_follow_up
from stock_research_llm_orchestrator.preparation.research_draft import (
    DraftInput,
    DraftOutput,
    build_bundle,
    run_codex,
    validate_bundle,
)
from stock_research_llm_orchestrator.preparation.research_follow_up import validate_follow_up
from stock_research_llm_orchestrator.preparation.storage import publish_preparation, read_bundle
from stock_research_llm_orchestrator.requests.storage import RuntimeStorageError


_DESTINATION_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")


def select_public_ir_request(files: dict[str, bytes], allowed_host: str) -> tuple[int, str]:
    """Choose the first new model-suggested URL on the approved host."""
    validate_bundle(files)
    data = DraftInput.model_validate_json(files["input.json"])
    output = DraftOutput.model_validate_json(files["draft.json"])
    seen = {item.source_url for item in data.evidence}
    for index, request in enumerate(output.follow_up_requests):
        url = request.suggested_source_url
        if url is None or url in seen:
            continue
        try:
            parsed = urlsplit(url)
        except ValueError:
            continue
        if parsed.scheme == "https" and parsed.hostname == allowed_host:
            return index, url
    raise ValueError("research_step_no_eligible_ir_request")


def advance_research_step(
    *,
    prior: Path,
    allowed_host: str,
    runtime: Path,
    runs: Path,
    follow_up_destination: str,
    draft_destination: str,
    task_id: str,
    approval_root: Path = Path("config/source-approvals"),
) -> tuple[Path, Path]:
    """Fetch one requested document and produce a new provisional LLM draft."""
    if (
        not _DESTINATION_NAME.fullmatch(follow_up_destination)
        or not _DESTINATION_NAME.fullmatch(draft_destination)
        or follow_up_destination == draft_destination
    ):
        raise ValueError("research_step_destination_invalid")
    prior_files = read_bundle(prior)
    index, url = select_public_ir_request(prior_files, allowed_host)
    if (runs / draft_destination).exists():
        raise FileExistsError("research_step_draft_exists")
    follow_up = acquire_public_ir_follow_up(
        prior=prior,
        url=url,
        allowed_host=allowed_host,
        request_index=index,
        runtime=runtime,
        runs=runs,
        destination=follow_up_destination,
        task_id=task_id,
    )
    follow_up_files = read_bundle(follow_up)
    validate_follow_up(follow_up_files)
    data = DraftInput.model_validate_json(follow_up_files["input.json"])
    draft = run_codex(data, approval_root)
    publish_preparation(runs, draft_destination, build_bundle(data, draft), validator=validate_bundle)
    return follow_up, runs / draft_destination


def main(argv: list[str] | None = None) -> int:
    """Advance one explicitly authorized public IR research step."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-network", action="store_true", required=True)
    parser.add_argument("--prior", type=Path, required=True)
    parser.add_argument("--allow-host", required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--runs", type=Path, required=True)
    parser.add_argument("--follow-up-destination", required=True)
    parser.add_argument("--draft-destination", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--approval-root", type=Path, default=Path("config/source-approvals"))
    args = parser.parse_args(argv)
    try:
        follow_up, draft = advance_research_step(
            prior=args.prior,
            allowed_host=args.allow_host,
            runtime=args.runtime,
            runs=args.runs,
            follow_up_destination=args.follow_up_destination,
            draft_destination=args.draft_destination,
            task_id=args.task_id,
            approval_root=args.approval_root,
        )
    except (ValueError, OSError, RuntimeError, RuntimeStorageError, KeyError, TypeError) as error:
        print(f"research_step_failed: {type(error).__name__}")
        return 1
    print(f"research_step: valid; follow_up={follow_up}; draft={draft}; analysis_ready=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
