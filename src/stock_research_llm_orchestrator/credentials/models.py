"""Non-secret credential-file policy and preflight results."""

from dataclasses import dataclass
from typing import Literal


CredentialPreflightReason = Literal[
    "ready",
    "invalid_path",
    "missing",
    "inspection_failed",
    "symlink_not_allowed",
    "not_regular_file",
    "empty_file",
    "unreadable",
    "invalid_permissions",
]


@dataclass(frozen=True)
class CredentialFilePolicy:
    """Metadata-only policy for an operator-mounted credential file.

    The default does not choose a platform-specific permission policy. Once the
    operator policy is approved, callers can reject selected POSIX permission
    bits through ``forbidden_mode_bits``.
    """

    forbidden_mode_bits: int = 0
    required_mode: int | None = None

    def __post_init__(self) -> None:
        """Reject values that are not POSIX permission-bit masks."""
        if isinstance(self.forbidden_mode_bits, bool) or not 0 <= self.forbidden_mode_bits <= 0o777:
            raise ValueError("invalid_credential_file_policy")
        if self.required_mode is not None and (
            isinstance(self.required_mode, bool) or not 0 <= self.required_mode <= 0o777
        ):
            raise ValueError("invalid_credential_file_policy")


@dataclass(frozen=True)
class CredentialPreflightResult:
    """Sanitized metadata-only result that never includes a path or value."""

    status: Literal["ready", "rejected"]
    reason_code: CredentialPreflightReason
