"""Synthetic source files validated with exact raw-byte hashes."""

from pathlib import Path

import pytest

from stock_research_llm_orchestrator.configuration import ConfigurationArtifact, validate_configuration


@pytest.fixture
def artifacts() -> tuple[ConfigurationArtifact, ...]:
    """Load the synthetic approved files through the real validator."""
    return validate_configuration(Path(__file__).parents[1] / "fixtures/config/coordinator")
