"""Run one trusted Agent CLI command without interpreting provider output."""

import os
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.agent_execution import AgentProcessState


@dataclass(frozen=True, slots=True)
class RunnerInvocation:
    """Command and isolated environment prepared by a trusted provider adapter."""

    argv: tuple[str, ...]
    cwd: Path
    env: Mapping[str, str]
    stdin_bytes: bytes


@dataclass(frozen=True, slots=True)
class RunnerStatus:
    """Observed OS process state without provider-specific interpretation."""

    agent_run_id: str
    process_state: AgentProcessState
    started_at: datetime
    ended_at: datetime | None
    exit_code: int | None
    cancellation_requested: bool


@dataclass(frozen=True, slots=True)
class CollectedRun:
    """Private output files and exact hashes for one finished process."""

    status: RunnerStatus
    stdout_path: Path
    stdout_sha256: str
    stderr_path: Path
    stderr_sha256: str


@dataclass(slots=True)
class _RunningProcess:
    process: subprocess.Popen[bytes]
    directory: Path
    started_at: datetime
    ended_at: datetime | None = None
    exit_code: int | None = None
    cancellation_requested: bool = False


class SessionRunner:
    """Manage local CLI runs while leaving sessions and response parsing to adapters."""

    def __init__(self, root: Path) -> None:
        """Use an existing trusted directory dedicated to agent process output."""
        if root.is_symlink() or not root.is_dir():
            raise ValueError("agent_runner_root_invalid")
        self.root = root
        self._runs: dict[str, _RunningProcess] = {}

    def start(self, invocation: RunnerInvocation) -> str:
        """Start a process and return its system-generated run ID immediately."""
        if not invocation.argv or any(not item or "\x00" in item for item in invocation.argv):
            raise ValueError("agent_runner_argv_invalid")
        if not invocation.cwd.is_dir() or invocation.cwd.is_symlink():
            raise ValueError("agent_runner_cwd_invalid")
        if any("\x00" in key or "\x00" in value for key, value in invocation.env.items()):
            raise ValueError("agent_runner_env_invalid")
        agent_run_id = f"agent-run-{uuid4().hex}"
        directory = self.root / agent_run_id
        directory.mkdir(mode=0o700)
        try:
            with (
                tempfile.TemporaryFile(mode="w+b", dir=directory) as stdin,
                _open_private_output(directory / "stdout.bin") as stdout,
                _open_private_output(directory / "stderr.bin") as stderr,
            ):
                stdin.write(invocation.stdin_bytes)
                stdin.seek(0)
                started_at = datetime.now(UTC)
                process = subprocess.Popen(
                    invocation.argv,
                    stdin=stdin,
                    stdout=stdout,
                    stderr=stderr,
                    cwd=invocation.cwd,
                    env=dict(invocation.env),
                    shell=False,
                )
        except OSError, ValueError:
            for name in ("stdout.bin", "stderr.bin"):
                (directory / name).unlink(missing_ok=True)
            directory.rmdir()
            raise RuntimeError("agent_process_start_failed") from None
        self._runs[agent_run_id] = _RunningProcess(process=process, directory=directory, started_at=started_at)
        return agent_run_id

    def get_status(self, agent_run_id: str) -> RunnerStatus:
        """Poll one known run and preserve the first observed terminal timestamp."""
        run = self._runs[agent_run_id]
        if run.ended_at is None:
            code = run.process.poll()
            if code is not None:
                run.exit_code = code
                run.ended_at = datetime.now(UTC)
        if run.ended_at is None:
            state = AgentProcessState.CANCELLING if run.cancellation_requested else AgentProcessState.RUNNING
        elif run.cancellation_requested:
            state = AgentProcessState.CANCELLED
        elif run.exit_code == 0:
            state = AgentProcessState.SUCCEEDED
        else:
            state = AgentProcessState.FAILED
        return RunnerStatus(
            agent_run_id=agent_run_id,
            process_state=state,
            started_at=run.started_at,
            ended_at=run.ended_at,
            exit_code=run.exit_code,
            cancellation_requested=run.cancellation_requested,
        )

    def cancel(self, agent_run_id: str) -> bool:
        """Request termination once; return whether cancellation was accepted."""
        run = self._runs[agent_run_id]
        if run.cancellation_requested:
            return True
        if self.get_status(agent_run_id).ended_at is not None:
            return False
        try:
            run.process.terminate()
        except ProcessLookupError:
            self.get_status(agent_run_id)
            return False
        run.cancellation_requested = True
        return True

    def collect(self, agent_run_id: str) -> CollectedRun:
        """Return exact private output locations only after process termination."""
        status = self.get_status(agent_run_id)
        if status.ended_at is None:
            raise ValueError("agent_run_not_finished")
        directory = self._runs[agent_run_id].directory
        stdout_path = directory / "stdout.bin"
        stderr_path = directory / "stderr.bin"
        return CollectedRun(
            status=status,
            stdout_path=stdout_path,
            stdout_sha256=_hash_file(stdout_path),
            stderr_path=stderr_path,
            stderr_sha256=_hash_file(stderr_path),
        )


def _hash_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _open_private_output(path: Path) -> BinaryIO:
    return os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb")
