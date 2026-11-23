"""Tests for deterministic public schema generation."""

import json
from pathlib import Path

from stock_research_llm_orchestrator.contracts.registry import ContractRegistry, default_registry
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
    path = Path("schemas/detailed-analysis/validation-error.schema.json")
    schema = json.loads(first[path])
    assert schema["$schema"] == JSON_SCHEMA_DIALECT
    assert schema["properties"]["schema_id"]["const"] == "detailed-analysis.validation-error"


def test_generation_keeps_only_the_latest_version_at_the_stable_path() -> None:
    """Publish one schema per contract when several runtime versions are registered."""
    schema_id = "detailed-analysis.validation-error"
    model = default_registry.get(schema_id, 1)
    registry = ContractRegistry()
    registry.register(schema_id, 2, model)
    registry.register(schema_id, 1, model)

    path = Path("schemas/detailed-analysis/validation-error.schema.json")
    documents = generate_schema_documents(registry)

    assert list(documents) == [path]
    assert json.loads(documents[path])["$id"] == f"urn:stock-research-llm-orchestrator:{schema_id}:v2"


def test_schema_check_reports_and_then_accepts_generated_file(tmp_path: Path) -> None:
    """Detect missing generated files and accept synchronized files."""
    expected_paths = [
        Path("schemas/detailed-analysis/acquisition-analysis.schema.json"),
        Path("schemas/detailed-analysis/acquisition-result.schema.json"),
        Path("schemas/detailed-analysis/acquisition-snapshot.schema.json"),
        Path("schemas/detailed-analysis/agent-configuration.schema.json"),
        Path("schemas/detailed-analysis/agent-execution.schema.json"),
        Path("schemas/detailed-analysis/audit-request.schema.json"),
        Path("schemas/detailed-analysis/audit-result.schema.json"),
        Path("schemas/detailed-analysis/common-evidence-update.schema.json"),
        Path("schemas/detailed-analysis/configured-analysis.schema.json"),
        Path("schemas/detailed-analysis/detailed-analysis-policy.schema.json"),
        Path("schemas/detailed-analysis/detailed-analysis-task.schema.json"),
        Path("schemas/detailed-analysis/dispute.schema.json"),
        Path("schemas/detailed-analysis/evidence-set.schema.json"),
        Path("schemas/detailed-analysis/execution-manifest.schema.json"),
        Path("schemas/detailed-analysis/external-request-event.schema.json"),
        Path("schemas/detailed-analysis/final-analysis-result.schema.json"),
        Path("schemas/detailed-analysis/human-decision.schema.json"),
        Path("schemas/detailed-analysis/instruction-application.schema.json"),
        Path("schemas/detailed-analysis/investment-conclusions.schema.json"),
        Path("schemas/detailed-analysis/market-profile.schema.json"),
        Path("schemas/detailed-analysis/operation-request.schema.json"),
        Path("schemas/detailed-analysis/operation-result.schema.json"),
        Path("schemas/detailed-analysis/primary-review.schema.json"),
        Path("schemas/detailed-analysis/research-context.schema.json"),
        Path("schemas/detailed-analysis/review-audit-policy.schema.json"),
        Path("schemas/detailed-analysis/review-response.schema.json"),
        Path("schemas/detailed-analysis/search-record.schema.json"),
        Path("schemas/detailed-analysis/session-continuation-policy.schema.json"),
        Path("schemas/detailed-analysis/source-approval.schema.json"),
        Path("schemas/detailed-analysis/source-profile.schema.json"),
        Path("schemas/detailed-analysis/specialist-analysis.schema.json"),
        Path("schemas/detailed-analysis/synthesis-result.schema.json"),
        Path("schemas/detailed-analysis/validation-error.schema.json"),
        Path("schemas/detailed-analysis/web-analysis.schema.json"),
        Path("schemas/detailed-analysis/web-research-policy.schema.json"),
        Path("schemas/detailed-analysis/worker-analysis.schema.json"),
    ]

    assert synchronize_schemas(tmp_path, check=True) == expected_paths
    assert synchronize_schemas(tmp_path, check=False) == []
    assert synchronize_schemas(tmp_path, check=True) == []
