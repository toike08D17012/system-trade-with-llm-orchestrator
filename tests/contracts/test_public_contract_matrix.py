"""Matrix tests covering generic validation failures for every public contract."""

import json
from pathlib import Path

import pytest

from stock_research_llm_orchestrator.contracts.errors import ErrorCode
from stock_research_llm_orchestrator.contracts.registry import default_registry
from stock_research_llm_orchestrator.contracts.validation.wrapper import (
    ContractValidationError,
    InputFormat,
    validate_text,
)


FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "contracts" / "detailed-analysis" / "v1"
SCHEMA_ROOT = Path(__file__).parents[2] / "schemas" / "detailed-analysis" / "v1"
FIXTURE_INDEX: dict[str, str] = json.loads((FIXTURE_ROOT / "fixture-index.json").read_text(encoding="utf-8"))
PUBLIC_CASES = tuple(sorted(FIXTURE_INDEX.items()))


def _payload(relative_path: str) -> dict[str, object]:
    return json.loads((FIXTURE_ROOT / relative_path).read_text(encoding="utf-8"))


def _assert_error(payload: dict[str, object], expected_code: ErrorCode) -> None:
    with pytest.raises(ContractValidationError) as captured:
        validate_text(json.dumps(payload), InputFormat.JSON)
    assert captured.value.artifact.code is expected_code


def test_fixture_index_matches_the_public_registry() -> None:
    """Require one fixed valid fixture for each exact public schema version."""
    registry_ids = {schema_id for (schema_id, version), _model in default_registry.items() if version == 1}

    assert set(FIXTURE_INDEX) == registry_ids
    assert len(FIXTURE_INDEX) == 28


@pytest.mark.parametrize(("schema_id", "relative_path"), PUBLIC_CASES)
def test_every_public_contract_has_a_valid_fixture(schema_id: str, relative_path: str) -> None:
    """Dispatch every indexed fixture through the common validation interface."""
    result = validate_text((FIXTURE_ROOT / relative_path).read_text(encoding="utf-8"), InputFormat.JSON)

    assert result.model_dump(mode="json")["schema_id"] == schema_id


@pytest.mark.parametrize(("schema_id", "relative_path"), PUBLIC_CASES)
def test_every_public_contract_rejects_a_missing_required_field(schema_id: str, relative_path: str) -> None:
    """Remove one contract-specific required field from every public fixture."""
    payload = _payload(relative_path)
    schema_path = SCHEMA_ROOT / f"{schema_id.removeprefix('detailed-analysis.')}.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    field = next(item for item in schema["required"] if item not in {"schema_id", "schema_version"})
    payload.pop(field)

    _assert_error(payload, ErrorCode.REQUIRED_FIELD_MISSING)


@pytest.mark.parametrize(("_schema_id", "relative_path"), PUBLIC_CASES)
def test_every_public_contract_rejects_schema_id_type_mismatch(_schema_id: str, relative_path: str) -> None:
    """Reject implicit conversion of the dispatch schema identifier."""
    payload = _payload(relative_path)
    payload["schema_id"] = 1

    _assert_error(payload, ErrorCode.TYPE_MISMATCH)


@pytest.mark.parametrize(("_schema_id", "relative_path"), PUBLIC_CASES)
def test_every_public_contract_rejects_unknown_fields(_schema_id: str, relative_path: str) -> None:
    """Reject extra fields without copying their values into public errors."""
    payload = _payload(relative_path)
    payload["unexpected_field"] = "must-not-be-copied"

    _assert_error(payload, ErrorCode.UNKNOWN_FIELD)


@pytest.mark.parametrize(("_schema_id", "relative_path"), PUBLIC_CASES)
def test_every_public_contract_rejects_unsupported_versions(_schema_id: str, relative_path: str) -> None:
    """Reject future or old contract versions without fallback or migration."""
    payload = _payload(relative_path)
    payload["schema_version"] = 2

    _assert_error(payload, ErrorCode.UNSUPPORTED_SCHEMA_VERSION)


@pytest.mark.parametrize(("_schema_id", "relative_path"), PUBLIC_CASES)
def test_every_public_contract_rejects_nonpositive_versions(_schema_id: str, relative_path: str) -> None:
    """Reject a nonpositive version before registry lookup."""
    payload = _payload(relative_path)
    payload["schema_version"] = 0

    _assert_error(payload, ErrorCode.CONSTRAINT_VIOLATION)
