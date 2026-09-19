"""Tests for the public command-line interface."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from stock_research_llm_orchestrator import cli
from stock_research_llm_orchestrator.errors import ApplicationError, ExitCode


def test_help_and_version_use_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    """Expose help and package version through argparse's stdout path."""
    with pytest.raises(SystemExit) as help_exit:
        cli.run(["--help"])
    assert help_exit.value.code == ExitCode.SUCCESS
    assert "config" in capsys.readouterr().out

    with pytest.raises(SystemExit) as version_exit:
        cli.run(["--version"])
    assert version_exit.value.code == ExitCode.SUCCESS
    assert "stock-research 0.1.0" in capsys.readouterr().out


def test_usage_errors_use_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    """Retain argparse exit code 2 and stderr usage errors."""
    with pytest.raises(SystemExit) as exc_info:
        cli.run(["config", "validate", "--log-level", "INFO"])
    assert exc_info.value.code == ExitCode.USAGE
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "unrecognized arguments" in captured.err


def test_validate_success_uses_input_display_path(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Print only the caller-provided path in the success summary."""
    monkeypatch.setattr(cli, "validate_configuration", lambda path: (object(), object()))
    assert cli.run(["config", "validate", "--config-dir", "relative-config"]) == ExitCode.SUCCESS
    assert capsys.readouterr().out == "Validated 2 configuration artifacts under relative-config.\n"


@pytest.mark.parametrize(
    ("error", "expected_code"),
    [
        (ApplicationError("safe failure", ExitCode.INVALID_CONFIGURATION), ExitCode.INVALID_CONFIGURATION),
        (ApplicationError("safe failure", ExitCode.CONFIGURATION_IO), ExitCode.CONFIGURATION_IO),
    ],
)
def test_main_maps_application_errors(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error: ApplicationError,
    expected_code: ExitCode,
) -> None:
    """Map safe application errors without exposing rejected input."""

    def fail(argv: object) -> int:
        del argv
        raise error

    monkeypatch.setattr(cli, "run", fail)
    assert cli.main([]) == expected_code
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "Error: safe failure\n"


def test_main_maps_interrupt_and_unexpected_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Map interrupts and hide unexpected exception values."""

    def interrupt(argv: object) -> int:
        del argv
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "run", interrupt)
    assert cli.main([]) == ExitCode.INTERRUPTED
    assert "Interrupted by user." in capsys.readouterr().err

    def crash(argv: object) -> int:
        del argv
        raise RuntimeError("sensitive-value")

    monkeypatch.setattr(cli, "run", crash)
    assert cli.main([]) == ExitCode.INTERNAL_ERROR
    assert "sensitive-value" not in capsys.readouterr().err


def test_default_config_path_is_relative_to_current_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pass the relative default to pathlib for current-directory resolution."""
    observed: list[Path] = []

    def validate(path: Path) -> tuple[object, ...]:
        observed.append(path)
        return (object(),)

    monkeypatch.setattr(cli, "validate_configuration", validate)
    assert cli.run(["config", "validate"]) == ExitCode.SUCCESS
    assert observed == [Path("config")]


@pytest.mark.parametrize("entrypoint", ["console", "module"])
def test_installed_entrypoints_launch_the_same_cli(entrypoint: str) -> None:
    """Launch both supported process entrypoints from the installed environment."""
    if entrypoint == "console":
        executable = shutil.which("stock-research")
        assert executable is not None
        command = [executable, "--version"]
    else:
        command = [sys.executable, "-m", "stock_research_llm_orchestrator", "--version"]
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    assert result.returncode == ExitCode.SUCCESS
    assert result.stdout == "stock-research 0.1.0\n"
    assert result.stderr == ""
