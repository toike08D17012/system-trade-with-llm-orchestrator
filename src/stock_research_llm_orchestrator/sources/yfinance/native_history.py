"""Native yfinance acquisition with a shared, durable library-call throttle."""

import fcntl
import json
import math
import os
import re
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Protocol

import pandas as pd  # type: ignore[import-untyped]
import yaml
import yfinance as yf  # type: ignore[import-untyped]
from yfinance.config import YfConfig  # type: ignore[import-untyped]
from yfinance.exceptions import YFRateLimitError  # type: ignore[import-untyped]

from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.policies import SourceApprovalV1
from stock_research_llm_orchestrator.sources.yfinance.private_runtime import private_yfinance_runtime


DEFAULT_STATE_ROOT = Path(__file__).resolve().parents[4] / "runs/.runtime/yfinance-native"
MIN_INTERVAL_SECONDS = 10.0
FAILURE_COOLDOWN_SECONDS = 900.0


def _validate_approval() -> None:
    """Preserve source authorization independently of legacy HTTP profiles."""
    path = Path(__file__).resolve().parents[4] / "config/source-approvals/yfinance/v3.yaml"
    try:
        approval = SourceApprovalV1.model_validate(yaml.safe_load(path.read_text()))
        today = datetime.now(UTC).date().isoformat()
        if not (
            approval.source_id == "yfinance"
            and approval.approval_version == 3
            and approval.online_use_allowed
            and approval.effective_on is not None
            and approval.recheck_due_on is not None
            and approval.effective_on <= today < approval.recheck_due_on
        ):
            raise ValueError
    except OSError, ValueError, yaml.YAMLError:
        raise HistoryAcquisitionError("yfinance_source_not_approved") from None


class HistoryAcquisitionError(ValueError):
    """A sanitized provider or local acquisition failure."""


class HistoryThrottledError(HistoryAcquisitionError):
    """A call was rejected before invoking the provider."""

    def __init__(self, reason: str, retry_at: float | None = None) -> None:
        """Expose a non-secret deadline without sleeping or retrying."""
        super().__init__(reason)
        self.retry_at = retry_at


class _Ticker(Protocol):
    def history(self, **kwargs: object) -> object: ...


@dataclass(frozen=True)
class HistoryResult:
    """Returned price data and reproducibility metadata, never HTTP artifacts."""

    frame: pd.DataFrame
    metadata: dict[str, object]


class NativeHistoryClient:
    """Serialize all native calls sharing this state root across Linux processes.

    Use in a dedicated, single-thread acquisition worker. The pinned library owns
    HTTP sessions and authentication. Limits count library calls, not HTTP sends.
    """

    def __init__(
        self,
        state_root: Path = DEFAULT_STATE_ROOT,
        *,
        ticker_factory: Callable[[str], _Ticker] = yf.Ticker,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """Bind shared provider state; never allocate state per task or symbol."""
        self.state_root = state_root
        self._ticker_factory = ticker_factory
        self._clock = clock

    @contextmanager
    def _admit(self) -> Iterator[None]:
        root = self.state_root
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if root.is_symlink() or root.stat().st_uid != os.getuid() or root.stat().st_mode & 0o077:
            raise HistoryAcquisitionError("unsafe_yfinance_state_root")
        fd = os.open(root / "provider.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "r+") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise HistoryThrottledError("yfinance_busy") from None
            state_path = root / "provider.json"
            now = self._clock()
            if state_path.exists():
                try:
                    state = json.loads(state_path.read_text())
                    next_at = float(state["next_at"])
                    recorded_at = float(state["recorded_at"])
                    if state["version"] != 1 or not all(map(math.isfinite, (next_at, recorded_at))):
                        raise ValueError
                    if next_at < recorded_at:
                        raise ValueError
                except ValueError, KeyError, TypeError:
                    raise HistoryAcquisitionError("invalid_yfinance_rate_state") from None
                if now < recorded_at:
                    raise HistoryThrottledError("yfinance_clock_moved_backwards", next_at)
                if state.get("running", False):
                    # The OS released a previous worker's lock without completion.
                    self._save_deadline(now, FAILURE_COOLDOWN_SECONDS)
                    raise HistoryThrottledError("yfinance_cooldown", now + FAILURE_COOLDOWN_SECONDS)
                if now < next_at:
                    raise HistoryThrottledError("yfinance_cooldown", next_at)
            # Commit before I/O: a killed worker leaves a conservative cooldown.
            self._save_deadline(now, FAILURE_COOLDOWN_SECONDS, running=True)
            try:
                yield
            except BaseException:
                self._save_deadline(max(now, self._clock()), FAILURE_COOLDOWN_SECONDS)
                raise
            else:
                self._save_deadline(max(now, self._clock()), MIN_INTERVAL_SECONDS)

    def _save_deadline(self, now: float, interval: float, *, running: bool = False) -> None:
        temporary = self.state_root / "provider.pending"
        fd = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump({"version": 1, "recorded_at": now, "next_at": now + interval, "running": running}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(self.state_root / "provider.json")
        directory = os.open(self.state_root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def history(
        self,
        symbol: str,
        *,
        period: str | None = None,
        start: str | None = None,
        end: str | None = None,
    ) -> HistoryResult:
        """Fetch one Tokyo daily series, without retries or a custom HTTP session.

        Direct symbol use verifies acquisition only, not JPX eligibility. Analysis
        callers must pass their verified intent through YfinanceDailyAdapter.
        """
        if not re.fullmatch(r"[0-9A-Z]{4}\.T", symbol):
            raise HistoryAcquisitionError("invalid_yfinance_symbol")
        if period is not None:
            if start is not None or end is not None or period not in {"1mo", "3mo", "6mo", "1y", "2y", "5y", "10y"}:
                raise HistoryAcquisitionError("invalid_yfinance_period")
            window: dict[str, object] = {"period": period}
        else:
            if start is None or end is None or date.fromisoformat(start) >= date.fromisoformat(end):
                raise HistoryAcquisitionError("invalid_yfinance_period")
            window = {"start": start, "end": end}
        arguments = {
            **window,
            "interval": "1d",
            "auto_adjust": False,
            "actions": True,
            "repair": False,
            "keepna": True,
            "timeout": 10,
        }
        _validate_approval()
        with self._admit(), private_yfinance_runtime():
            previous_retries = YfConfig.network.retries
            previous_hide = YfConfig.debug.hide_exceptions
            try:
                YfConfig.network.retries = 0
                YfConfig.debug.hide_exceptions = False
                ticker = self._ticker_factory(symbol)
                frame = ticker.history(**arguments)
                if not isinstance(frame, pd.DataFrame) or frame.empty:
                    raise HistoryAcquisitionError("yfinance_empty_history")
                required = {"Open", "High", "Low", "Close", "Adj Close", "Volume", "Dividends", "Stock Splits"}
                if not required.issubset(frame.columns) or not isinstance(frame.index, pd.DatetimeIndex):
                    raise HistoryAcquisitionError("yfinance_invalid_history")
                if frame.index.has_duplicates or not frame.index.is_monotonic_increasing or frame["Close"].isna().all():
                    raise HistoryAcquisitionError("yfinance_invalid_history")
            except YFRateLimitError:
                raise HistoryAcquisitionError("yfinance_rate_limited") from None
            except HistoryAcquisitionError:
                raise
            except Exception:
                raise HistoryAcquisitionError("yfinance_history_failed") from None
            finally:
                YfConfig.network.retries = previous_retries
                YfConfig.debug.hide_exceptions = previous_hide
        return HistoryResult(
            frame=frame,
            metadata={
                "source": "Yahoo Finance",
                "library": "yfinance",
                "library_version": yf.__version__,
                "function": "Ticker.history",
                "symbol": symbol,
                "arguments": arguments,
                "retrieved_at": datetime.fromtimestamp(self._clock(), UTC).isoformat(),
                "policy": "yfinance-native-history-v1",
                "source_approval_version": 3,
                "rate_unit": "library_call",
                "http_body_retained": False,
            },
        )
