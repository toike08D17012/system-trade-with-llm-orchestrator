"""Metadata-only credential-file preflight.

The preflight deliberately never opens the file. The EDINET physical transport
must perform its own race-safe open and metadata verification immediately before
use.
"""

import os
import stat
from collections.abc import Callable
from pathlib import Path

from stock_research_llm_orchestrator.credentials.models import CredentialFilePolicy, CredentialPreflightResult


ReadableCheck = Callable[[Path], bool]


def preflight_credential_file(
    path: str | os.PathLike[str],
    *,
    policy: CredentialFilePolicy | None = None,
    readable_check: ReadableCheck | None = None,
) -> CredentialPreflightResult:
    """Validate credential-file metadata without opening or retaining the file.

    Args:
        path: Non-secret operator-supplied file location.
        policy: Optional approved metadata policy. No permission bits are rejected
            until a caller supplies an explicit policy.
        readable_check: Injectable metadata-level readability check for deterministic
            offline tests.

    Returns:
        A sanitized result containing no path, file content, or exception text.
    """
    active_policy = policy or CredentialFilePolicy()
    check_readable = readable_check or _is_readable
    try:
        candidate = Path(path)
    except TypeError, ValueError, OSError:
        return CredentialPreflightResult("rejected", "invalid_path")

    try:
        metadata = candidate.lstat()
    except FileNotFoundError:
        return CredentialPreflightResult("rejected", "missing")
    except OSError:
        return CredentialPreflightResult("rejected", "inspection_failed")

    if stat.S_ISLNK(metadata.st_mode):
        return CredentialPreflightResult("rejected", "symlink_not_allowed")
    if not stat.S_ISREG(metadata.st_mode):
        return CredentialPreflightResult("rejected", "not_regular_file")
    if metadata.st_size <= 0:
        return CredentialPreflightResult("rejected", "empty_file")
    mode = stat.S_IMODE(metadata.st_mode)
    if mode & active_policy.forbidden_mode_bits or (
        active_policy.required_mode is not None and mode != active_policy.required_mode
    ):
        return CredentialPreflightResult("rejected", "invalid_permissions")
    try:
        if not check_readable(candidate):
            return CredentialPreflightResult("rejected", "unreadable")
    except Exception:  # The injected OS boundary is untrusted and must be sanitized.
        return CredentialPreflightResult("rejected", "inspection_failed")
    return CredentialPreflightResult("ready", "ready")


def _is_readable(path: Path) -> bool:
    """Check readability without following a symlink or opening the file."""
    return os.access(path, os.R_OK, follow_symlinks=False)
