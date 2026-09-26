"""Offline acceptance harness checks; real network is never used by this suite."""

import hashlib
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yfinance.cache as cache  # type: ignore[import-untyped]
import yfinance.data as data  # type: ignore[import-untyped]
from curl_cffi import requests

from stock_research_llm_orchestrator.sources.yfinance.live_probe import run_probe
from stock_research_llm_orchestrator.sources.yfinance.private_runtime import (
    private_runtime_active,
    private_yfinance_runtime,
)


@pytest.mark.parametrize(
    "status,media", [(200, "text/plain"), (404, "text/html"), (302, "text/html"), (200, "application/octet-stream")]
)
def test_probe_persists_metadata_without_body_or_cookie(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    media: str,
) -> None:
    """Known failures stop instead of triggering a retry or saving raw auxiliary bytes."""
    calls: list[str] = []
    secret = b"unique-secret-live-probe-canary"

    def send(session: requests.Session, method: str, url: str, **kwargs: Any) -> requests.Response:
        assert private_runtime_active()
        assert kwargs["allow_redirects"] is False
        calls.append(url)
        session.cookies.set("B", secret.decode())
        kwargs["content_callback"](secret)
        response = requests.Response()
        response.status_code = status
        response.url = url + "/" if url == "https://fc.yahoo.com" else url
        response.headers["Content-Type"] = media
        response.headers["Set-Cookie"] = secret.decode()
        return response

    monkeypatch.setattr(requests.Session, "request", send)
    summary = run_probe(tmp_path / "probe", allow_network=True)
    success = status == 200 and media == "text/plain"
    assert summary["status"] == ("passed" if success else "failed")
    assert len(calls) == (2 if success or status == 404 else 1)
    if status == 404:
        assert summary["observations"][0]["reason_code"] == "cookie_404_continue"  # type: ignore[index]
    assert summary["full_download_verified"] is False
    for path in (tmp_path / "probe").rglob("*"):
        if path.is_file():
            assert secret not in path.read_bytes()
            assert hashlib.sha256(secret).hexdigest().encode() not in path.read_bytes()


def test_probe_requires_opt_in_before_creating_state(tmp_path: Path) -> None:
    """No network or filesystem setup occurs without explicit authorization."""
    with pytest.raises(ValueError, match="opt_in"):
        run_probe(tmp_path / "probe")
    assert not (tmp_path / "probe").exists()


@pytest.mark.parametrize("with_cookie", [True, False])
@pytest.mark.parametrize("crumb_status", [200, 429])
def test_cookie_404_continues_to_crumb_without_requiring_cookie(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    with_cookie: bool,
    crumb_status: int,
) -> None:
    """Cookie absence alone never prevents the next approved request."""
    calls: list[str] = []

    def send(session: requests.Session, method: str, url: str, **kwargs: Any) -> requests.Response:
        calls.append(url)
        cookie_request = url == "https://fc.yahoo.com"
        if cookie_request and with_cookie:
            session.cookies.set("B", "ephemeral-canary")
        kwargs["content_callback"](b"not found" if cookie_request else b"ephemeral-crumb")
        response = requests.Response()
        response.status_code = 404 if cookie_request else crumb_status
        response.url = url
        response.headers["Content-Type"] = "text/html" if cookie_request or crumb_status == 429 else "text/plain"
        return response

    monkeypatch.setattr(requests.Session, "request", send)
    summary = run_probe(tmp_path / "probe", allow_network=True)
    assert summary["status"] == ("passed" if crumb_status == 200 else "failed")
    assert len(calls) == 2


def test_private_runtime_restores_globals_and_blocks_concurrent_entry(caplog: pytest.LogCaptureFixture) -> None:
    """Cache writes and all upstream logs are suppressed only inside the isolated scope."""
    previous_cookie = cache._CookieCacheManager._Cookie_cache
    previous_instances = data.SingletonMeta._instances
    previous_logging = logging.root.manager.disable
    with private_yfinance_runtime():
        assert private_runtime_active()
        cache.get_cookie_cache().store("curlCffi", {"secret": "canary"})
        assert cache.get_cookie_cache().lookup("curlCffi") is None
        logging.getLogger("yfinance").error("secret-log-canary")
        with ThreadPoolExecutor(max_workers=1) as executor:

            def enter() -> None:
                with private_yfinance_runtime():
                    pytest.fail("concurrent runtime entered")

            with pytest.raises(ValueError, match="already_active"):
                executor.submit(enter).result()
    assert not private_runtime_active()
    assert cache._CookieCacheManager._Cookie_cache is previous_cookie
    assert data.SingletonMeta._instances is previous_instances
    assert logging.root.manager.disable == previous_logging
    assert "secret-log-canary" not in caplog.text


def test_private_runtime_restores_state_even_if_cookie_cleanup_fails() -> None:
    """Cleanup errors cannot leave a live capability or disabled logging behind."""
    previous_logging = logging.root.manager.disable
    previous_instances = data.SingletonMeta._instances

    def fail() -> None:
        raise RuntimeError("cleanup_failed")

    with pytest.raises(RuntimeError, match="cleanup_failed"), private_yfinance_runtime():
        data.SingletonMeta._instances[object] = SimpleNamespace(
            _session=SimpleNamespace(cookies=SimpleNamespace(clear=fail)),
            _cookie="canary",
            _crumb="canary",
        )
    assert logging.root.manager.disable == previous_logging
    assert data.SingletonMeta._instances is previous_instances
    assert not private_runtime_active()
    with private_yfinance_runtime():
        assert private_runtime_active()
