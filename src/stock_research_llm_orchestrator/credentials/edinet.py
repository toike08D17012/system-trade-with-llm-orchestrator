"""Race-safe, send-time EDINET credential use boundary."""

import os
import stat
from collections.abc import Callable
from pathlib import Path


_MAX_CREDENTIAL_BYTES = 512
_REQUIRED_MODE = 0o600


class CredentialAccessError(RuntimeError):
    """Sanitized failure at the send-time credential boundary."""


def use_edinet_api_key_for_send[ResultT](
    path: str | os.PathLike[str],
    send: Callable[[str], ResultT],
) -> ResultT:
    """Validate and use an EDINET key without returning or retaining its value.

    The opened descriptor is authoritative; metadata-only preflight is not a
    substitute for these checks. The callback must apply the key only to the
    physical EDINET request and must not serialize, log, or retain it.

    Args:
        path: Operator-mounted credential file path.
        send: Physical-send callback receiving the validated key.

    Returns:
        The callback result, which must not contain the credential value.

    Raises:
        CredentialAccessError: If metadata, content, or callback processing fails.
    """
    if not hasattr(os, "O_NOFOLLOW"):
        raise CredentialAccessError("credential_platform_unsupported")
    try:
        candidate = Path(path)
        descriptor = os.open(candidate, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    except TypeError, ValueError, OSError:
        raise CredentialAccessError("credential_access_failed") from None

    payload = bytearray()
    try:
        before = os.fstat(descriptor)
        _validate_metadata(before)
        payload = _read_bounded(descriptor)
        after = os.fstat(descriptor)
        if (before.st_dev, before.st_ino, before.st_mode, before.st_uid) != (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_uid,
        ) or after.st_size != len(payload):
            raise CredentialAccessError("credential_changed_during_read")
        key = _decode_key(payload)
        try:
            return send(key)
        except Exception:
            raise CredentialAccessError("credential_use_failed") from None
    except CredentialAccessError:
        raise
    except OSError, UnicodeError:
        raise CredentialAccessError("credential_access_failed") from None
    finally:
        for index in range(len(payload)):
            payload[index] = 0
        os.close(descriptor)


def _validate_metadata(metadata: os.stat_result) -> None:
    """Validate the opened file descriptor without including metadata in errors."""
    if not stat.S_ISREG(metadata.st_mode):
        raise CredentialAccessError("credential_not_regular_file")
    if stat.S_IMODE(metadata.st_mode) != _REQUIRED_MODE or metadata.st_uid != os.geteuid():
        raise CredentialAccessError("credential_permissions_invalid")
    if not 0 < metadata.st_size <= _MAX_CREDENTIAL_BYTES:
        raise CredentialAccessError("credential_size_invalid")


def _read_bounded(descriptor: int) -> bytearray:
    """Read at most the approved size plus one byte from one descriptor."""
    payload = bytearray()
    while True:
        chunk = os.read(descriptor, _MAX_CREDENTIAL_BYTES + 1 - len(payload))
        if not chunk:
            return payload
        payload.extend(chunk)
        if len(payload) > _MAX_CREDENTIAL_BYTES:
            raise CredentialAccessError("credential_size_invalid")


def _decode_key(payload: bytearray) -> str:
    """Decode the approved single-line UTF-8 credential format."""
    try:
        value = payload.decode("utf-8")
    except UnicodeDecodeError:
        raise CredentialAccessError("credential_format_invalid") from None
    if value.endswith("\n"):
        value = value[:-1]
    if not value or "\n" in value or "\r" in value or "\x00" in value:
        raise CredentialAccessError("credential_format_invalid")
    return value
