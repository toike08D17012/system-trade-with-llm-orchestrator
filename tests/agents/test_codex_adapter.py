"""Codex command and completed response boundaries."""

import json
import sys
import time
from pathlib import Path
from uuid import uuid4

import pytest

from stock_research_llm_orchestrator.agents.codex_adapter import codex_invocation, collect_codex_turn
from stock_research_llm_orchestrator.agents.session_runner import RunnerInvocation, SessionRunner


def _completed_runner(tmp_path: Path, body: bytes, exit_code: int = 0) -> tuple[SessionRunner, str]:
    root = tmp_path / "output"
    root.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    runner = SessionRunner(root)
    script = "import sys;sys.stdout.buffer.write(bytes.fromhex(sys.argv[1]));sys.exit(int(sys.argv[2]))"
    invocation = RunnerInvocation((sys.executable, "-c", script, body.hex(), str(exit_code)), workspace, {}, b"")
    run_id = runner.start(invocation)
    deadline = time.monotonic() + 5
    while runner.get_status(run_id).ended_at is None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert runner.get_status(run_id).ended_at is not None
    return runner, run_id


def _valid_body(session_id: str) -> bytes:
    events = (
        {"type": "thread.started", "thread_id": session_id},
        {"type": "turn.started"},
        {"type": "item.completed", "item": {"type": "agent_message", "text": "OK"}},
        {"type": "turn.completed", "usage": {"input_tokens": 3}},
    )
    return ("\n".join(json.dumps(event) for event in events) + "\n").encode()


def test_new_and_resumed_invocations_keep_prompt_private_and_read_only(tmp_path: Path) -> None:
    """Command arguments carry mode and exact session, while stdin carries the prompt."""
    env = {"CODEX_HOME": str(tmp_path / "codex-home")}
    new = codex_invocation(Path("/usr/bin/codex"), tmp_path, env, "Analyze this")
    assert new.argv[-1] == "-"
    assert new.argv[new.argv.index("--sandbox") :][:2] == ("--sandbox", "read-only")
    assert "Analyze this" not in new.argv
    assert new.stdin_bytes == b"Analyze this"
    assert new.env is env

    session_id = str(uuid4())
    resumed = codex_invocation(Path("/usr/bin/codex"), tmp_path, env, "Continue", session_id)
    assert resumed.argv[-2:] == (session_id, "-")
    assert 'sandbox_mode="read-only"' in resumed.argv
    assert resumed.stdin_bytes == b"Continue"
    with pytest.raises(ValueError, match="codex_resume_session_id_invalid"):
        codex_invocation(Path("codex"), tmp_path, env, "Continue", "--last")
    with pytest.raises(ValueError, match="codex_prompt_empty"):
        codex_invocation(Path("codex"), tmp_path, env, "  ")


def test_collect_adopts_only_successful_matching_session(tmp_path: Path) -> None:
    """A failed process or unexpected resumed session cannot become a completed turn."""
    session_id = str(uuid4())
    runner, run_id = _completed_runner(tmp_path, _valid_body(session_id))
    assert collect_codex_turn(runner, run_id, session_id).final_text == "OK"
    with pytest.raises(ValueError, match="codex_resume_session_mismatch"):
        collect_codex_turn(runner, run_id, str(uuid4()))


def test_collect_rejects_nonzero_process_before_parsing(tmp_path: Path) -> None:
    """An otherwise well-formed stream is invalid after a failed CLI exit."""
    runner, run_id = _completed_runner(tmp_path, _valid_body(str(uuid4())), exit_code=1)
    with pytest.raises(ValueError, match="codex_turn_process_failed"):
        collect_codex_turn(runner, run_id)
