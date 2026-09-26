"""Regression tests for third-party dependency import boundaries."""

from importlib import import_module
from importlib.metadata import version
from pathlib import Path


def test_external_requests_and_yfinance_are_not_shadowed_by_test_packages() -> None:
    """Import dependencies from their installed distributions, not the test tree."""
    requests = import_module("requests")
    yfinance = import_module("yfinance")
    tests_root = Path(__file__).resolve().parents[1]
    assert requests.__file__ is not None
    assert yfinance.__file__ is not None
    requests_path = Path(requests.__file__).resolve()
    yfinance_path = Path(yfinance.__file__).resolve()

    assert not requests_path.is_relative_to(tests_root)
    assert not yfinance_path.is_relative_to(tests_root)
    assert requests.__version__ == version("requests")
    assert yfinance.__version__ == version("yfinance")
