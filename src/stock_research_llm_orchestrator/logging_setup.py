"""Application logging initialization."""

import logging
import sys
from datetime import UTC, datetime


PACKAGE_LOGGER_NAME = "stock_research_llm_orchestrator"
_HANDLER_MARKER = "stock-research-stderr"


class _UtcFormatter(logging.Formatter):
    """Format timestamps as UTC ISO-8601 values."""

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:  # noqa: N802
        del datefmt
        return datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def configure_logging(level: str = "WARNING") -> logging.Logger:
    """Configure one idempotent stderr handler for the package logger."""
    logger = logging.getLogger(PACKAGE_LOGGER_NAME)
    logger.setLevel(logging.getLevelName(level))
    logger.propagate = False

    handlers = [handler for handler in logger.handlers if getattr(handler, "name", None) == _HANDLER_MARKER]
    if handlers:
        handler = handlers[0]
        handler.setLevel(logging.NOTSET)
        return logger

    handler = logging.StreamHandler(sys.stderr)
    handler.name = _HANDLER_MARKER
    handler.setFormatter(_UtcFormatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    return logger
