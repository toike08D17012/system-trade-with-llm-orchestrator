"""Tests for deterministic public schema generation."""

import json
from pathlib import Path

from stock_research_llm_orchestrator.contracts.schema_generation import (
    JSON_SCHEMA_DIALECT,
    generate_schema_documents,
    synchronize_schemas,
)


def test_generated_schema_is_deterministic_and_uses_approved_dialect() -> None:
    """Generate stable JSON Schema 2020-12 documents."""
    first = generate_schema_documents()
    second = generate_schema_documents()

    assert first == second
    path = Path("schemas/detailed-analysis/v1/validation-error.schema.json")
    schema = json.loads(first[path])
    assert schema["$schema"] == JSON_SCHEMA_DIALECT
    assert schema["properties"]["schema_id"]["const"] == "detailed-analysis.validation-error"


def test_schema_check_reports_and_then_accepts_generated_file(tmp_path: Path) -> None:
    """Detect missing generated files and accept synchronized files."""
    expected_path = Path("schemas/detailed-analysis/v1/validation-error.schema.json")

    assert synchronize_schemas(tmp_path, check=True) == [expected_path]
    assert synchronize_schemas(tmp_path, check=False) == []
    assert synchronize_schemas(tmp_path, check=True) == []
