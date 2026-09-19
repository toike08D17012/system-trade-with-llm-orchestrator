"""Tests for application logging setup."""

import logging

from stock_research_llm_orchestrator.logging_setup import PACKAGE_LOGGER_NAME, configure_logging


def test_logging_setup_is_idempotent_and_uses_utc() -> None:
    """Install one package-owned stderr handler with UTC timestamps."""
    logger = logging.getLogger(PACKAGE_LOGGER_NAME)
    original_handlers = logger.handlers[:]
    original_level = logger.level
    original_propagate = logger.propagate
    try:
        logger.handlers.clear()
        configure_logging("INFO")
        configure_logging("ERROR")
        assert logger.level == logging.ERROR
        assert logger.propagate is False
        assert len(logger.handlers) == 1
        formatter = logger.handlers[0].formatter
        assert formatter is not None
        record = logging.LogRecord(PACKAGE_LOGGER_NAME, logging.ERROR, "", 0, "message", (), None)
        assert formatter.format(record).split()[0].endswith("Z")
    finally:
        logger.handlers[:] = original_handlers
        logger.setLevel(original_level)
        logger.propagate = original_propagate
