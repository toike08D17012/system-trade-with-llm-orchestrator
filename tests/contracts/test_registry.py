"""Tests for exact-version contract registry behavior."""

import pytest

from stock_research_llm_orchestrator.contracts.errors import ValidationErrorV1
from stock_research_llm_orchestrator.contracts.registry import (
    ContractRegistry,
    DuplicateContractError,
    UnsupportedContractError,
)


def test_registry_rejects_duplicate_key() -> None:
    """Reject duplicate schema identifier and version registrations."""
    registry = ContractRegistry()
    registry.register("detailed-analysis.validation-error", 1, ValidationErrorV1)

    with pytest.raises(DuplicateContractError):
        registry.register("detailed-analysis.validation-error", 1, ValidationErrorV1)


def test_registry_does_not_fallback_to_another_version() -> None:
    """Require an exact schema version match."""
    registry = ContractRegistry()
    registry.register("detailed-analysis.validation-error", 1, ValidationErrorV1)

    with pytest.raises(UnsupportedContractError):
        registry.get("detailed-analysis.validation-error", 2)
