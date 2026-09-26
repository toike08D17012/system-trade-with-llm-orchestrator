"""Ephemeral upstream state for an explicitly authorized verification process."""

import logging
from collections.abc import Iterator
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from contextvars import ContextVar
from io import TextIOBase
from threading import Lock

import yfinance as yf  # type: ignore[import-untyped]
import yfinance.cache as cache  # type: ignore[import-untyped]
import yfinance.data as data  # type: ignore[import-untyped]


_ACTIVE: ContextVar[bool] = ContextVar("yahoo_private_runtime", default=False)
_LOCK = Lock()


class _DiscardOutput(TextIOBase):
    def write(self, value: str) -> int:
        return len(value)


def private_runtime_active() -> bool:
    """Report whether library cache/log protections are installed in this context."""
    return _ACTIVE.get()


@contextmanager
def private_yfinance_runtime() -> Iterator[None]:
    """Isolate pinned library globals; use only in a dedicated verification process."""
    if yf.__version__ != "1.7.0":
        raise ValueError("unsupported_yfinance_version")
    if not _LOCK.acquire(blocking=False):
        raise ValueError("yahoo_private_runtime_already_active")
    old_cookie = cache._CookieCacheManager._Cookie_cache
    old_tz = cache._TzCacheManager._tz_cache
    old_instances = data.SingletonMeta._instances
    old_logging = logging.root.manager.disable
    cache._CookieCacheManager._Cookie_cache = cache._CookieCacheDummy()
    cache._TzCacheManager._tz_cache = cache._TzCacheDummy()
    data.SingletonMeta._instances = {}
    data.YfData.cache_get.cache_clear()
    logging.disable(logging.CRITICAL)
    token = _ACTIVE.set(True)
    try:
        with redirect_stdout(_DiscardOutput()), redirect_stderr(_DiscardOutput()):
            yield
    finally:
        try:
            for instance in data.SingletonMeta._instances.values():
                instance._crumb = None
                instance._cookie = None
                if instance._session is not None:
                    instance._session.cookies.clear()
            data.YfData.cache_get.cache_clear()
        finally:
            data.SingletonMeta._instances = old_instances
            cache._CookieCacheManager._Cookie_cache = old_cookie
            cache._TzCacheManager._tz_cache = old_tz
            logging.disable(old_logging)
            _ACTIVE.reset(token)
            _LOCK.release()
