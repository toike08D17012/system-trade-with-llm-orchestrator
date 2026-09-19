"""Tests that approved catalog names match implemented P1 artifacts."""

import json
import re
from pathlib import Path

from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.instructions import InstructionSetDefinitionV1
from stock_research_llm_orchestrator.contracts.registry import default_registry


REPOSITORY_ROOT = Path(__file__).parents[2]
CATALOG_PATH = REPOSITORY_ROOT / "docs" / "system-requirements" / "09-detailed-analysis-contract-catalog.md"
SCHEMA_ROOT = REPOSITORY_ROOT / "schemas" / "detailed-analysis" / "v1"
FIXTURE_ROOT = REPOSITORY_ROOT / "tests" / "fixtures" / "contracts" / "detailed-analysis" / "v1"
INSTRUCTION_ROOT = REPOSITORY_ROOT / "agent-sources" / "detailed-analysis" / "v1"
TEMPLATE_ROOT = REPOSITORY_ROOT / "templates" / "detailed-analysis" / "v1"


def test_catalog_public_schema_names_match_generated_files_and_fixtures() -> None:
    """Keep the approved 28-name catalog aligned with registry, files, and fixture index."""
    catalog = CATALOG_PATH.read_text(encoding="utf-8")
    catalog_names = set(re.findall(r"`([a-z0-9-]+\.schema\.json)`", catalog))
    generated_names = {path.name for path in SCHEMA_ROOT.glob("*.schema.json")}
    registry_names = {
        f"{schema_id.removeprefix('detailed-analysis.')}.schema.json"
        for (schema_id, _version), _model in default_registry.items()
    }
    fixture_ids = set(json.loads((FIXTURE_ROOT / "fixture-index.json").read_text(encoding="utf-8")))
    fixture_names = {f"{schema_id.removeprefix('detailed-analysis.')}.schema.json" for schema_id in fixture_ids}

    assert catalog_names == generated_names == registry_names == fixture_names
    assert len(catalog_names) == 28


def test_catalog_templates_and_instruction_sources_exist() -> None:
    """Resolve every version 1 template and Agent instruction named by the catalog."""
    catalog = CATALOG_PATH.read_text(encoding="utf-8")
    template_names = {"final-report.md.j2", "human-decision-request.md.j2"}
    instruction_names = {
        "common.md",
        "orchestrator.md",
        "codex-worker.md",
        "claude-worker.md",
        "primary-reviewer.md",
        "antigravity-auditor.md",
        "instruction-set.json",
    }

    for name in template_names:
        assert f"`{name}`" in catalog
        assert (TEMPLATE_ROOT / name).is_file()
    for name in instruction_names:
        assert f"`{name}`" in catalog
        assert (INSTRUCTION_ROOT / name).is_file()


def test_instruction_outputs_are_registered_public_contracts() -> None:
    """Prevent role instructions from naming an unregistered output contract."""
    definition = InstructionSetDefinitionV1.model_validate_json(
        (INSTRUCTION_ROOT / "instruction-set.json").read_text(encoding="utf-8")
    )
    registered_ids = {schema_id for (schema_id, version), _model in default_registry.items() if version == 1}

    for role in definition.roles.values():
        assert set(role.output_schema_ids).issubset(registered_ids)
