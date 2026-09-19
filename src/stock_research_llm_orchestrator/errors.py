"""Application-level errors and process exit codes."""

from enum import IntEnum


class ExitCode(IntEnum):
    """Stable process exit codes exposed by the application CLI."""

    SUCCESS = 0
    USAGE = 2
    INVALID_CONFIGURATION = 3
    CONFIGURATION_IO = 4
    INTERNAL_ERROR = 70
    INTERRUPTED = 130


class ApplicationError(Exception):
    """A user-safe application failure with a stable exit code."""

    def __init__(self, message: str, exit_code: ExitCode) -> None:
        """Initialize an error whose message is safe for stderr."""
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code
