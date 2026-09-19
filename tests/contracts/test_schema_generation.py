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
    expected_paths = [
        Path("schemas/detailed-analysis/v1/agent-execution.schema.json"),
        Path("schemas/detailed-analysis/v1/audit-request.schema.json"),
        Path("schemas/detailed-analysis/v1/audit-result.schema.json"),
        Path("schemas/detailed-analysis/v1/common-evidence-update.schema.json"),
        Path("schemas/detailed-analysis/v1/detailed-analysis-policy.schema.json"),
        Path("schemas/detailed-analysis/v1/detailed-analysis-task.schema.json"),
        Path("schemas/detailed-analysis/v1/dispute.schema.json"),
        Path("schemas/detailed-analysis/v1/evidence-set.schema.json"),
        Path("schemas/detailed-analysis/v1/execution-manifest.schema.json"),
        Path("schemas/detailed-analysis/v1/external-request-event.schema.json"),
        Path("schemas/detailed-analysis/v1/final-analysis-result.schema.json"),
        Path("schemas/detailed-analysis/v1/human-decision.schema.json"),
        Path("schemas/detailed-analysis/v1/instruction-application.schema.json"),
        Path("schemas/detailed-analysis/v1/market-profile.schema.json"),
        Path("schemas/detailed-analysis/v1/operation-request.schema.json"),
        Path("schemas/detailed-analysis/v1/operation-result.schema.json"),
        Path("schemas/detailed-analysis/v1/primary-review.schema.json"),
        Path("schemas/detailed-analysis/v1/research-context.schema.json"),
        Path("schemas/detailed-analysis/v1/review-audit-policy.schema.json"),
        Path("schemas/detailed-analysis/v1/review-response.schema.json"),
        Path("schemas/detailed-analysis/v1/search-record.schema.json"),
        Path("schemas/detailed-analysis/v1/session-continuation-policy.schema.json"),
        Path("schemas/detailed-analysis/v1/source-approval.schema.json"),
        Path("schemas/detailed-analysis/v1/source-profile.schema.json"),
        Path("schemas/detailed-analysis/v1/synthesis-result.schema.json"),
        Path("schemas/detailed-analysis/v1/validation-error.schema.json"),
        Path("schemas/detailed-analysis/v1/web-research-policy.schema.json"),
        Path("schemas/detailed-analysis/v1/worker-analysis.schema.json"),
    ]

    assert synchronize_schemas(tmp_path, check=True) == expected_paths
    assert synchronize_schemas(tmp_path, check=False) == []
    assert synchronize_schemas(tmp_path, check=True) == []
