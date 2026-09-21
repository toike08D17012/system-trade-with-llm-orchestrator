"""Verify metadata-only credential preflight and sanitized failures."""

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from stock_research_llm_orchestrator.credentials import (
    CredentialFilePolicy,
    CredentialPreflightResult,
    preflight_credential_file,
)


CANARY = "dummy-edinet-key-do-not-log"


def test_accepts_non_empty_regular_file_without_reading_value(tmp_path: Path) -> None:
    """Accept metadata without returning the path or dummy file content."""
    credential = tmp_path / "edinet-key"
    credential.write_text(CANARY, encoding="utf-8")

    result = preflight_credential_file(credential)

    assert (result.status, result.reason_code) == ("ready", "ready")
    _assert_canary_absent(result)


@pytest.mark.parametrize(
    "setup,reason_code",
    [
        ("missing", "missing"),
        ("directory", "not_regular_file"),
        ("empty", "empty_file"),
        ("symlink", "symlink_not_allowed"),
    ],
)
def test_rejects_unsupported_file_metadata(tmp_path: Path, setup: str, reason_code: str) -> None:
    """Reject unsafe metadata without exposing the operator path."""
    credential = tmp_path / f"{CANARY}-{setup}"
    if setup == "directory":
        credential.mkdir()
    elif setup == "empty":
        credential.touch()
    elif setup == "symlink":
        target = tmp_path / "target"
        target.write_text(CANARY, encoding="utf-8")
        credential.symlink_to(target)

    result = preflight_credential_file(credential)

    assert (result.status, result.reason_code) == ("rejected", reason_code)
    _assert_canary_absent(result)


def test_applies_only_explicit_permission_policy(tmp_path: Path) -> None:
    """Avoid inventing a mode policy while supporting a later approved one."""
    credential = tmp_path / "edinet-key"
    credential.write_text(CANARY, encoding="utf-8")
    credential.chmod(0o640)

    assert preflight_credential_file(credential).status == "ready"
    result = preflight_credential_file(credential, policy=CredentialFilePolicy(forbidden_mode_bits=0o040))

    assert (result.status, result.reason_code) == ("rejected", "invalid_permissions")
    _assert_canary_absent(result)


def test_applies_explicit_exact_permission_policy(tmp_path: Path) -> None:
    """Require mode 0600 only when the approved caller policy asks for it."""
    credential = tmp_path / "edinet-key"
    credential.write_text(CANARY, encoding="utf-8")
    credential.chmod(0o640)

    rejected = preflight_credential_file(credential, policy=CredentialFilePolicy(required_mode=0o600))
    credential.chmod(0o600)
    accepted = preflight_credential_file(credential, policy=CredentialFilePolicy(required_mode=0o600))

    assert (rejected.status, rejected.reason_code) == ("rejected", "invalid_permissions")
    assert (accepted.status, accepted.reason_code) == ("ready", "ready")


@pytest.mark.parametrize("behavior", ["false", "raises"])
def test_sanitizes_readability_failures(tmp_path: Path, behavior: str) -> None:
    """Do not preserve path or exception text from the OS boundary."""
    credential = tmp_path / CANARY
    credential.write_text(CANARY, encoding="utf-8")

    def check(_: Path) -> bool:
        if behavior == "raises":
            raise OSError(CANARY)
        return False

    result = preflight_credential_file(credential, readable_check=check)

    expected = "inspection_failed" if behavior == "raises" else "unreadable"
    assert (result.status, result.reason_code) == ("rejected", expected)
    _assert_canary_absent(result)


@pytest.mark.parametrize(
    "field,value", [("forbidden_mode_bits", -1), ("required_mode", 0o1000), ("required_mode", True)]
)
def test_rejects_invalid_permission_policy(field: str, value: int) -> None:
    """Reject malformed masks with a stable non-sensitive error."""
    with pytest.raises(ValueError, match="^invalid_credential_file_policy$"):
        CredentialFilePolicy(**{field: value})


def _assert_canary_absent(result: CredentialPreflightResult) -> None:
    rendered = (repr(result), json.dumps(asdict(result)))
    assert all(CANARY not in item for item in rendered)
