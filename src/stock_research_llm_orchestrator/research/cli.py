"""Public entrypoints for provisional live execution and explicit offline replay."""

import argparse
import json
import re
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from threading import Lock
from time import monotonic
from typing import Literal
from uuid import uuid4

from stock_research_llm_orchestrator.agents.configuration import load_agent_configuration
from stock_research_llm_orchestrator.agents.research_provider import (
    ProviderAuthenticationError,
    ProviderExecutionError,
    ProviderResponseValidationError,
    WebTurnResult,
    invoke_research,
    invoke_web_research,
    preflight_research,
)
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.task import DetailedAnalysisTaskV1
from stock_research_llm_orchestrator.errors import ApplicationError, ExitCode
from stock_research_llm_orchestrator.preparation.research_draft import DraftInput, validate_transfer
from stock_research_llm_orchestrator.preparation.storage import publish_preparation, read_bundle
from stock_research_llm_orchestrator.research.input import build_input, serialize
from stock_research_llm_orchestrator.research.workflow import ResearchRequest, run_workflow, validate_bundle


def add_commands(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Add a minimal research command group without changing configuration commands."""
    research = commands.add_parser("research", help="Run or replay provisional independent research.")
    actions = research.add_subparsers(dest="research_command", required=True)
    for name in ("run", "replay"):
        command = actions.add_parser(name)
        command.add_argument("--task", type=Path, required=True)
        command.add_argument("--prior", type=Path, required=True)
        command.add_argument("--runs", type=Path, required=True)
        command.add_argument("--destination", required=True)
        command.add_argument(
            "--agents", type=Path, help="Role-agent YAML profile; selects independent Web research for live execution."
        )
        command.add_argument(
            "--workflow-version",
            type=int,
            choices=(1, 2),
            default=2,
            help="2: objective specialists (default); 1: legacy independent analysis.",
        )
        command.add_argument("--policy", type=Path, default=Path("config/policies/detailed-analysis/v1.yaml"))
        command.add_argument("--approval-root", type=Path, default=Path("config/source-approvals"))
        if name == "run":
            command.add_argument(
                "--runtime", type=Path, default=Path("runtime"), help="Shared durable acquisition state directory."
            )
            command.add_argument(
                "--resume-from",
                type=Path,
                help="Reuse a validated saved baseline; restart independent role invocations.",
            )
            command.add_argument(
                "--resume-completed-turns",
                action="store_true",
                help="Reuse only exactly matching completed provider calls from --resume-from.",
            )
        if name == "replay":
            command.add_argument("--responses", type=Path, required=True)
            command.add_argument("--at", required=True, help="Exact snapshot timestamp for offline replay.")
    validate = actions.add_parser("validate")
    validate.add_argument("--bundle", type=Path, required=True)


def execute(args: argparse.Namespace) -> int:
    """Publish only a complete, validated provisional bundle and return safe failures."""
    try:
        if args.research_command == "validate":
            destination = args.bundle
            validate_bundle(read_bundle(destination))
        else:
            configuration = load_agent_configuration(args.agents) if args.agents is not None else None
            resumed_inputs: dict[str, bytes] | None = None
            resumed_baseline: dict[str, bytes] | None = None
            if args.research_command == "run" and args.resume_completed_turns and args.resume_from is None:
                raise ValueError("research_resume_source_required")
            if args.research_command == "run" and args.resume_from is not None:
                from stock_research_llm_orchestrator.research.resume import load_resume

                resumed_inputs, resumed_config, resumed_baseline = load_resume(args.resume_from)
                if configuration is None or serialize(configuration) != serialize(resumed_config):
                    raise ValueError("research_resume_configuration_mismatch")
            if configuration is not None and args.workflow_version != 2:
                raise ValueError("research_agents_legacy_version_conflict")
            destination = args.runs / args.destination
            if (
                not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", args.destination)
                or not args.runs.is_dir()
                or destination.exists()
                or destination.is_symlink()
            ):
                raise ValueError("research_destination_invalid")
            prior = read_bundle(args.prior)
            data = DraftInput.model_validate_json(prior["input.json"])
            approvals = {}
            for item in data.evidence:
                path = (
                    Path("docs/decisions/0007-use-llm-led-evidence-follow-up.md")
                    if item.source_id == "issuer-ir"
                    else args.approval_root / item.source_id / f"v{item.approval_version}.yaml"
                )
                approvals[f"{item.source_id}-v{item.approval_version}"] = path.read_bytes()
            inputs = build_input(
                args.task.read_bytes(),
                prior,
                args.policy.read_bytes(),
                approvals,
                args.at
                if args.research_command == "replay"
                else json.loads(resumed_inputs["evidence-set.json"])["evidence_frozen_at"]
                if resumed_inputs is not None
                else datetime.now(UTC).isoformat(),
            )
            if resumed_inputs is not None and inputs != resumed_inputs:
                raise ValueError("research_resume_input_mismatch")
            mode: Literal["live", "replay"]
            responses: dict[str, bytes] = {}
            if args.research_command == "replay":
                responses = read_bundle(args.responses)

                def invoke(request: ResearchRequest) -> bytes:
                    return responses[f"{request.stage}.json"]

                mode = "replay"
            else:
                validate_transfer(data, args.approval_root)
                if configuration is None:
                    preflight_research()
                else:
                    preflight_research(configuration)

                def invoke(request: ResearchRequest) -> bytes:
                    validate_transfer(data, args.approval_root)
                    return invoke_research(request)

                mode = "live"
            if configuration is not None:
                from stock_research_llm_orchestrator.research.configured import replay_saved_bundle
                from stock_research_llm_orchestrator.research.configured import run_workflow as run_configured

                if mode == "live":
                    from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.acquisition_analysis import (
                        AcquisitionRequest,
                        AcquisitionResult,
                    )
                    from stock_research_llm_orchestrator.preparation.research_acquisition import (
                        ResearchAcquisitionExecutor,
                    )
                    from stock_research_llm_orchestrator.research.acquisition import run_workflow as run_acquisition
                    from stock_research_llm_orchestrator.research.baseline import prepare_baseline
                    from stock_research_llm_orchestrator.research.resume import (
                        load_acquisition_turns,
                        load_completed_turns,
                        load_retained_acquisitions,
                    )

                    execution_profile: str | None = "parallel_incremental_v5"
                    if args.resume_from is not None:
                        source_options = args.resume_from / "runtime-options.json"
                        execution_profile = (
                            json.loads(source_options.read_bytes()).get("execution_profile")
                            if source_options.is_file()
                            else None
                        )
                    completed_turns, resume_stamps = (
                        load_completed_turns(args.resume_from, include_pending_snapshots=True)
                        if args.resume_completed_turns and execution_profile is not None
                        else load_completed_turns(args.resume_from)
                        if args.resume_completed_turns
                        else ({}, {})
                    )
                    acquisition_checkpoints = (
                        load_acquisition_turns(args.resume_from) if args.resume_completed_turns else []
                    )
                    reassessment_from_epoch = (
                        max(
                            (
                                int(json.loads(turn.request)["data"].get("evidence_epoch", 1))
                                for turn in completed_turns.values()
                            ),
                            default=1,
                        )
                        + 1
                    )
                    stable_evidence_from_epoch = reassessment_from_epoch if completed_turns else 1
                    batch_common_updates_from_epoch = reassessment_from_epoch if completed_turns else 1
                    if args.resume_completed_turns:
                        saved_options = args.resume_from / "runtime-options.json"
                        if saved_options.is_file():
                            saved_epoch = json.loads(saved_options.read_bytes())["reassessment_from_epoch"]
                            if type(saved_epoch) is not int or saved_epoch < 1:
                                raise ValueError("research_resume_invalid_reassessment_epoch")
                            reassessment_from_epoch = saved_epoch
                            saved_stable_epoch = json.loads(saved_options.read_bytes()).get(
                                "stable_evidence_from_epoch", stable_evidence_from_epoch
                            )
                            if type(saved_stable_epoch) is not int or saved_stable_epoch < 1:
                                raise ValueError("research_resume_invalid_stable_evidence_epoch")
                            stable_evidence_from_epoch = saved_stable_epoch
                            saved_batch_epoch = json.loads(saved_options.read_bytes()).get(
                                "batch_common_updates_from_epoch", batch_common_updates_from_epoch
                            )
                            if type(saved_batch_epoch) is not int or saved_batch_epoch < 1:
                                raise ValueError("research_resume_invalid_batch_common_updates_epoch")
                            batch_common_updates_from_epoch = saved_batch_epoch
                    completed_keys = list(completed_turns)
                    completed_position = 0
                    consumed_completed: set[str] = set()
                    journal_lock = Lock()
                    acquisition_position = 0

                    executor = ResearchAcquisitionExecutor(
                        task=DetailedAnalysisTaskV1.model_validate_json(inputs["task.json"]),
                        runtime=args.runtime,
                        runs=args.runtime / "acquisition-runs",
                        evaluated_at=datetime.now(UTC),
                        approval_root=args.approval_root,
                        wait_for_gate=True,
                        retained_acquisitions=load_retained_acquisitions(args.resume_from)
                        if args.resume_from is not None
                        else (),
                    )
                    journal = args.runtime / "research-executions" / (args.destination + "-" + uuid4().hex)
                    journal.mkdir(mode=0o700, parents=True)
                    (journal / "runtime-options.json").write_text(
                        json.dumps(
                            {
                                "reassessment_from_epoch": reassessment_from_epoch,
                                "stable_evidence_from_epoch": stable_evidence_from_epoch,
                                "batch_common_updates_from_epoch": batch_common_updates_from_epoch,
                                **({"execution_profile": execution_profile} if execution_profile is not None else {}),
                            }
                        ),
                        encoding="utf-8",
                    )
                    for name, body in inputs.items():
                        path = journal / "input" / name
                        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                        path.write_bytes(body)
                    (journal / "agent-configuration.json").write_bytes(serialize(configuration))
                    if resumed_inputs is not None:
                        (journal / "resume.json").write_text(
                            json.dumps(
                                {
                                    "source": str(args.resume_from),
                                    "mode": "exact_completed_turns"
                                    if args.resume_completed_turns
                                    else "retained_baseline_fresh_role_invocations",
                                    "artifact_timestamp_fallback": "recorded_provider_response_capture_time",
                                }
                            ),
                            encoding="utf-8",
                        )
                    turn_count = 0
                    acquisition_count = 0

                    def acquire(
                        request: AcquisitionRequest, role: str, turn: int
                    ) -> tuple[AcquisitionResult, dict[str, bytes]]:
                        nonlocal acquisition_count, acquisition_position
                        index = acquisition_count
                        acquisition_count += 1
                        prefix = journal / "acquisitions" / str(index)
                        prefix.mkdir(mode=0o700, parents=True)
                        (prefix / "request.json").write_bytes(request.model_dump_json(indent=2).encode())
                        if acquisition_position < len(acquisition_checkpoints):
                            saved_request, result, payload = acquisition_checkpoints[acquisition_position]
                            if request.model_dump_json() != saved_request.model_dump_json():
                                raise ValueError("research_resume_acquisition_sequence_mismatch")
                            acquisition_position += 1
                            (prefix / "retained-checkpoint.json").write_text(
                                json.dumps({"source": str(args.resume_from), "index": acquisition_position - 1}),
                                encoding="utf-8",
                            )
                        else:
                            result, payload = executor(request, role, turn)
                        (prefix / "result.json").write_bytes(result.model_dump_json(indent=2).encode())
                        for name, body in payload.items():
                            path = prefix / "files" / name
                            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                            path.write_bytes(body)
                        return result, payload

                    def recorded_invoke(request: ResearchRequest) -> WebTurnResult:
                        nonlocal turn_count, completed_position
                        with journal_lock:
                            index = turn_count
                            turn_count += 1
                        started_at = datetime.now(UTC).isoformat()
                        started_monotonic = monotonic()
                        prefix = journal / f"{index:04d}-{request.stage}"
                        prefix.mkdir(mode=0o700)
                        record = {
                            "stage": request.stage,
                            "instructions": request.instructions,
                            "data": request.data,
                            "output_schema": request.response_model.model_json_schema(),
                            "agent": request.agent.model_dump(mode="json") if request.agent else None,
                        }
                        (prefix / "request.json").write_text(
                            json.dumps(
                                record,
                                ensure_ascii=False,
                                indent=2,
                            ),
                            encoding="utf-8",
                        )
                        reuse_completed = False
                        try:
                            snapshot = request.data.get("evidence_snapshot")
                            snapshot_ref = snapshot.get("reference") if isinstance(snapshot, dict) else None
                            key = str(snapshot_ref["artifact_id"]) if isinstance(snapshot_ref, dict) else request.stage
                            reuse_completed = (
                                key in completed_turns
                                if execution_profile is not None
                                else completed_position < len(completed_keys)
                            )
                            if reuse_completed:
                                if execution_profile is None and completed_keys[completed_position] != key:
                                    raise ValueError("research_resume_turn_sequence_mismatch")
                                retained = completed_turns[key]
                                if json.dumps(json.loads(retained.request), sort_keys=True) != json.dumps(
                                    record, sort_keys=True
                                ):
                                    raise ValueError("research_resume_request_mismatch")
                                request.response_model.model_validate_json(retained.response)
                                turn = WebTurnResult(retained.response, retained.trace, retained.request)
                                with journal_lock:
                                    if key in consumed_completed:
                                        raise ValueError("research_resume_duplicate_turn")
                                    consumed_completed.add(key)
                                    completed_position += 1
                                (prefix / "source-timestamps.json").write_text(
                                    json.dumps(
                                        {
                                            "provider_response_captured_at": retained.captured_at,
                                            "artifact_created_at": resume_stamps.get("artifact-" + key),
                                        }
                                    ),
                                    encoding="utf-8",
                                )
                                (prefix / "retained-checkpoint.json").write_text(
                                    json.dumps(
                                        {
                                            "source": str(retained.source),
                                            "request_sha256": sha256(retained.request).hexdigest(),
                                        }
                                    ),
                                    encoding="utf-8",
                                )
                                print(f"research: retained_completed_turn={key}", flush=True)
                            else:
                                captured_at = datetime.now(UTC).isoformat()
                                if execution_profile in {
                                    "parallel_incremental_v3",
                                    "parallel_incremental_v4",
                                    "parallel_incremental_v5",
                                } and request.stage.startswith("document_summary-"):
                                    from stock_research_llm_orchestrator.research.summary_cache import cached_summary

                                    cached = cached_summary(
                                        request, args.runtime / "document-summaries", invoke_web_research
                                    )
                                    turn = cached.turn
                                    captured_at = cached.captured_at
                                    reuse_completed = cached.cache_hit
                                    if cached.cache_hit:
                                        (prefix / "summary-cache.json").write_text(
                                            json.dumps({"key": request.stage, "captured_at": captured_at}),
                                            encoding="utf-8",
                                        )
                                else:
                                    turn = invoke_web_research(request)
                                    captured_at = datetime.now(UTC).isoformat()
                                (prefix / "source-timestamps.json").write_text(
                                    json.dumps({"provider_response_captured_at": captured_at}),
                                    encoding="utf-8",
                                )
                        except (ValueError, RuntimeError, OSError) as error:
                            if isinstance(error, ProviderExecutionError):
                                (prefix / "trace.jsonl").write_bytes(error.trace)
                                (prefix / "provider-failure.json").write_text(
                                    json.dumps(
                                        {"exit_status": error.exit_status, "diagnostic_code": error.diagnostic_code}
                                    ),
                                    encoding="utf-8",
                                )
                            if isinstance(error, ProviderResponseValidationError):
                                (prefix / "response.json").write_bytes(error.body)
                                (prefix / "trace.jsonl").write_bytes(error.trace)
                                (prefix / "validation-problems.json").write_text(
                                    json.dumps(error.problems, ensure_ascii=False, indent=2), encoding="utf-8"
                                )
                            (prefix / "failure.json").write_text(
                                json.dumps({"status": "incomplete", "error_type": type(error).__name__}),
                                encoding="utf-8",
                            )
                            raise
                        finally:
                            (prefix / "execution-timing.json").write_text(
                                json.dumps(
                                    {
                                        "started_at": started_at,
                                        "completed_at": datetime.now(UTC).isoformat(),
                                        "elapsed_seconds": monotonic() - started_monotonic,
                                        "measurement": "retained_response_reuse"
                                        if reuse_completed
                                        else "provider_call_envelope",
                                    }
                                ),
                                encoding="utf-8",
                            )
                        (prefix / "response.json").write_bytes(turn.body)
                        (prefix / "trace.jsonl").write_bytes(turn.trace)
                        return turn

                    print(f"research: running; execution_record={journal}", flush=True)
                    baseline = (
                        resumed_baseline
                        if resumed_baseline is not None
                        else prepare_baseline(inputs, configuration, acquire, recorded_invoke)
                    )
                    for name, body in baseline.items():
                        path = journal / "baseline" / name
                        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                        path.write_bytes(body)
                    from stock_research_llm_orchestrator.research.pdf_reader import build_pdf_reader

                    files = run_acquisition(
                        inputs,
                        configuration,
                        recorded_invoke,
                        Path("agent-sources/detailed-analysis/acquisition-v1"),
                        mode="live",
                        acquire=acquire,
                        baseline_files=baseline,
                        build_pdf_reader=lambda _item, raw: build_pdf_reader(raw, args.runtime / "pdf-readers"),
                        resume_stamps=resume_stamps or None,
                        reassessment_from_epoch=reassessment_from_epoch,
                        stable_evidence_from_epoch=stable_evidence_from_epoch,
                        batch_common_updates_from_epoch=batch_common_updates_from_epoch,
                        **({"execution_profile": execution_profile} if execution_profile is not None else {}),
                    )
                    if json.loads(files["manifest.json"])["status"] == "stalled":
                        publish_preparation(journal, "stalled", files, validator=validate_bundle)
                        raise RuntimeError("research_stalled_without_completed_report")
                elif "manifest.json" in responses and json.loads(responses["manifest.json"]).get("format_version") == 5:
                    from stock_research_llm_orchestrator.research.acquisition import replay_saved_bundle as replay_new

                    files = replay_new(responses, inputs, configuration)
                elif "manifest.json" in responses and json.loads(responses["manifest.json"]).get("format_version") == 4:
                    from stock_research_llm_orchestrator.research.web import replay_saved_bundle as replay_web

                    files = replay_web(responses, inputs, configuration)
                elif "manifest.json" in responses:
                    files = replay_saved_bundle(responses, inputs, configuration)
                else:
                    files = run_configured(
                        inputs, configuration, invoke, Path("agent-sources/detailed-analysis/configured-v1"), mode=mode
                    )
            elif args.workflow_version == 2:
                from stock_research_llm_orchestrator.research.objectives import run_workflow as run_objectives

                files = run_objectives(inputs, invoke, Path("agent-sources/detailed-analysis/objectives-v1"), mode=mode)
            else:
                files = run_workflow(inputs, invoke, Path("agent-sources/detailed-analysis/v1"), mode=mode)
            publish_preparation(args.runs, args.destination, files, validator=validate_bundle)
        print(f"research: valid; bundle={destination}; analysis_ready=false; analysis_completed=false")
        return ExitCode.SUCCESS
    except ValueError, KeyError, TypeError:
        raise ApplicationError(
            "Research input or response validation failed.", ExitCode.INVALID_CONFIGURATION
        ) from None
    except OSError:
        raise ApplicationError("Research file or executable is unavailable.", ExitCode.CONFIGURATION_IO) from None
    except ProviderAuthenticationError as error:
        raise ApplicationError(
            f"Authentication is required for the {error.provider} CLI in this execution environment.",
            ExitCode.INVALID_CONFIGURATION,
        ) from None
    except RuntimeError:
        raise ApplicationError(
            "Research provider execution failed; no completed bundle was published.", ExitCode.INTERNAL_ERROR
        ) from None
