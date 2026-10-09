"""Local process acceptance for the provider-independent agent runner."""

import stat
import sys
import time
from hashlib import sha256
from pathlib import Path

import pytest

from stock_research_llm_orchestrator.agents.session_runner import RunnerInvocation, SessionRunner
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.agent_execution import AgentProcessState


def _finished(runner: SessionRunner, agent_run_id: str) -> AgentProcessState:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        state = runner.get_status(agent_run_id).process_state
        if state in {AgentProcessState.SUCCEEDED, AgentProcessState.FAILED, AgentProcessState.CANCELLED}:
            return state
        time.sleep(0.01)
    pytest.fail("local test process did not terminate")


def test_runner_preserves_exact_output_and_does_not_persist_stdin(tmp_path: Path) -> None:
    """Return one run ID and exact private output without interpreting provider text."""
    root = tmp_path / "agent-output"
    root.mkdir()
    runner = SessionRunner(root)
    script = (
        "import sys; data=sys.stdin.buffer.read(); "
        "sys.stdout.buffer.write(str(len(data)).encode()); sys.stderr.write('warning')"
    )
    invocation = RunnerInvocation((sys.executable, "-c", script), tmp_path, {}, b"private prompt")
    first = runner.start(invocation)
    second = runner.start(invocation)
    assert first != second
    for agent_run_id in (first, second):
        assert _finished(runner, agent_run_id) is AgentProcessState.SUCCEEDED
        result = runner.collect(agent_run_id)
        assert result.stdout_path.read_bytes() == b"14"
        assert result.stderr_path.read_bytes() == b"warning"
        assert result.stdout_sha256 == sha256(b"14").hexdigest()
        assert result.status.exit_code == 0
        assert result.status.ended_at is not None
        assert not runner.cancel(agent_run_id)
        assert stat.S_IMODE(result.stdout_path.stat().st_mode) == 0o600
        assert {path.name for path in result.stdout_path.parent.iterdir()} == {"stdout.bin", "stderr.bin"}


def test_runner_distinguishes_failure_and_cancellation(tmp_path: Path) -> None:
    """Keep nonzero exit and requested cancellation as different terminal outcomes."""
    root = tmp_path / "agent-output"
    root.mkdir()
    runner = SessionRunner(root)
    failed = runner.start(RunnerInvocation((sys.executable, "-c", "import sys;sys.exit(7)"), tmp_path, {}, b""))
    assert _finished(runner, failed) is AgentProcessState.FAILED
    assert runner.collect(failed).status.exit_code == 7

    sleeping = runner.start(RunnerInvocation((sys.executable, "-c", "import time;time.sleep(60)"), tmp_path, {}, b""))
    try:
        assert runner.get_status(sleeping).process_state is AgentProcessState.RUNNING
        with pytest.raises(ValueError, match="agent_run_not_finished"):
            runner.collect(sleeping)
        assert runner.cancel(sleeping)
        assert runner.cancel(sleeping)
        assert _finished(runner, sleeping) is AgentProcessState.CANCELLED
        assert runner.collect(sleeping).status.cancellation_requested
    finally:
        runner.cancel(sleeping)


def test_runner_start_failure_removes_private_files_without_echoing_environment(tmp_path: Path) -> None:
    """A failed trusted command leaves no partial run or secret-bearing error text."""
    root = tmp_path / "agent-output"
    root.mkdir()
    runner = SessionRunner(root)
    invocation = RunnerInvocation(
        (str(tmp_path / "missing-cli"),), tmp_path, {"PRIVATE_VALUE": "do-not-print"}, b"input"
    )
    with pytest.raises(RuntimeError, match="^agent_process_start_failed$"):
        runner.start(invocation)
    assert list(root.iterdir()) == []
