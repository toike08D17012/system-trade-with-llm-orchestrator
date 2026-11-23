"""Build restricted Codex CLI turns and adopt only completed JSONL responses."""

from collections.abc import Mapping
from pathlib import Path
from uuid import UUID

from stock_research_llm_orchestrator.agents.codex_response import CodexTurnObservation, parse_codex_jsonl
from stock_research_llm_orchestrator.agents.session_runner import RunnerInvocation, SessionRunner
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.agent_execution import AgentProcessState


def codex_invocation(
    executable: Path,
    workspace: Path,
    env: Mapping[str, str],
    prompt: str,
    resume_session_id: str | None = None,
) -> RunnerInvocation:
    """Prepare one read-only Codex turn with a prompt passed through private stdin."""
    if not prompt.strip():
        raise ValueError("codex_prompt_empty")
    if resume_session_id is not None:
        try:
            UUID(resume_session_id)
        except ValueError:
            raise ValueError("codex_resume_session_id_invalid") from None
    shared = (
        "--json",
        "-c",
        'web_search="live"',
        "--ignore-user-config",
        "--ignore-rules",
        "--skip-git-repo-check",
    )
    if resume_session_id is None:
        argv = (str(executable), "exec", *shared, "--sandbox", "read-only", "-C", str(workspace), "-")
    else:
        argv = (
            str(executable),
            "exec",
            "resume",
            *shared,
            "-c",
            'sandbox_mode="read-only"',
            resume_session_id,
            "-",
        )
    return RunnerInvocation(argv=argv, cwd=workspace, env=env, stdin_bytes=prompt.encode("utf-8"))


def collect_codex_turn(
    runner: SessionRunner, agent_run_id: str, expected_session_id: str | None = None
) -> CodexTurnObservation:
    """Parse a successful turn, binding a resumed turn to its requested session."""
    result = runner.collect(agent_run_id)
    if result.status.process_state is not AgentProcessState.SUCCEEDED:
        raise ValueError("codex_turn_process_failed")
    observation = parse_codex_jsonl(result.stdout_path.read_bytes())
    if expected_session_id is not None and observation.provider_session_id != expected_session_id:
        raise ValueError("codex_resume_session_mismatch")
    return observation
