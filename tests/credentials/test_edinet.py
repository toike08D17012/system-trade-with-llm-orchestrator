"""Verify the send-time EDINET credential boundary with dummy values only."""

from pathlib import Path

import pytest

from stock_research_llm_orchestrator.credentials.edinet import CredentialAccessError, use_edinet_api_key_for_send


CANARY = "dummy-edinet-key-do-not-log"


@pytest.mark.parametrize("suffix", ["", "\n"])
def test_uses_one_line_key_only_inside_callback(tmp_path: Path, suffix: str) -> None:
    """Pass the validated dummy key to one callback without returning it."""
    credential = _credential(tmp_path, (CANARY + suffix).encode())
    observed = False

    def send(key: str) -> str:
        nonlocal observed
        observed = key == CANARY
        return "sent"

    assert use_edinet_api_key_for_send(credential, send) == "sent"
    assert observed


@pytest.mark.parametrize(
    "payload",
    [b"", b"x" * 513, b"\xff", b"first\nsecond", b"value\r\n", b"value\x00suffix"],
)
def test_rejects_invalid_content_without_exposing_it(tmp_path: Path, payload: bytes) -> None:
    """Reject invalid dummy formats with sanitized errors and no callback."""
    credential = _credential(tmp_path, payload)
    called = False

    def send(_: str) -> None:
        nonlocal called
        called = True

    with pytest.raises(CredentialAccessError) as captured:
        use_edinet_api_key_for_send(credential, send)

    assert not called
    assert CANARY not in str(captured.value)
    assert CANARY not in repr(captured.value)


def test_rejects_symlink_directory_and_insecure_mode(tmp_path: Path) -> None:
    """Fail closed on unsafe descriptor metadata before callback use."""
    target = _credential(tmp_path, CANARY.encode())
    symlink = tmp_path / "symlink"
    symlink.symlink_to(target)
    directory = tmp_path / "directory"
    directory.mkdir()
    insecure = _credential(tmp_path, CANARY.encode(), name="insecure")
    insecure.chmod(0o640)

    for candidate in (symlink, directory, insecure):
        with pytest.raises(CredentialAccessError) as captured:
            use_edinet_api_key_for_send(candidate, _unexpected_send)
        assert CANARY not in str(captured.value)


def test_sanitizes_callback_exception(tmp_path: Path) -> None:
    """Prevent callback exception text from carrying the credential outward."""
    credential = _credential(tmp_path, CANARY.encode())

    def send(key: str) -> None:
        raise RuntimeError(key)

    with pytest.raises(CredentialAccessError, match="^credential_use_failed$") as captured:
        use_edinet_api_key_for_send(credential, send)

    assert CANARY not in str(captured.value)
    assert CANARY not in repr(captured.value)


def _credential(tmp_path: Path, payload: bytes, *, name: str = "edinet-key") -> Path:
    path = tmp_path / name
    path.write_bytes(payload)
    path.chmod(0o600)
    return path


def _unexpected_send(_: str) -> None:
    raise AssertionError("send callback must not run")
