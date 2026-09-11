"""Registry for exact contract version dispatch."""

from collections.abc import Iterator

from stock_research_llm_orchestrator.contracts.base import StrictContractModel
from stock_research_llm_orchestrator.contracts.errors import ValidationErrorV1


type ContractKey = tuple[str, int]
type ContractModel = type[StrictContractModel]


class DuplicateContractError(ValueError):
    """Raised when a contract key is registered more than once."""


class UnsupportedContractError(LookupError):
    """Raised when an exact contract key is unavailable."""


class ContractRegistry:
    """Map stable schema identifiers and versions to Pydantic models."""

    def __init__(self) -> None:
        """Create an empty registry."""
        self._models: dict[ContractKey, ContractModel] = {}

    def register(self, schema_id: str, schema_version: int, model: ContractModel) -> None:
        """Register one exact contract key.

        Args:
            schema_id: Stable schema identifier without a version suffix.
            schema_version: Positive integer contract version.
            model: Strict Pydantic contract model.

        Raises:
            DuplicateContractError: If the key is already registered.
            ValueError: If the key is structurally invalid.
        """
        if not schema_id:
            raise ValueError("schema_id must not be empty")
        if schema_version < 1:
            raise ValueError("schema_version must be positive")
        key = (schema_id, schema_version)
        if key in self._models:
            raise DuplicateContractError(f"contract is already registered: {schema_id} v{schema_version}")
        self._models[key] = model

    def get(self, schema_id: str, schema_version: int) -> ContractModel:
        """Return the model for an exact key without fallback."""
        try:
            return self._models[(schema_id, schema_version)]
        except KeyError as exc:
            raise UnsupportedContractError(f"unsupported contract: {schema_id} v{schema_version}") from exc

    def items(self) -> Iterator[tuple[ContractKey, ContractModel]]:
        """Iterate over contracts in deterministic key order."""
        for key in sorted(self._models):
            yield key, self._models[key]


default_registry = ContractRegistry()
default_registry.register("detailed-analysis.validation-error", 1, ValidationErrorV1)
