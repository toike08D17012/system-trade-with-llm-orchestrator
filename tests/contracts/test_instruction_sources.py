"""Tests for repository-managed detailed-analysis instruction sources."""

from pathlib import Path

from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.instructions import (
    InstructionSetDefinitionV1,
    compose_instruction_text,
    instruction_text_sha256,
)


REPOSITORY_ROOT = Path(__file__).parents[2]
INSTRUCTION_ROOT = REPOSITORY_ROOT / "agent-sources" / "detailed-analysis" / "v1"


def _definition() -> InstructionSetDefinitionV1:
    return InstructionSetDefinitionV1.model_validate_json(
        (INSTRUCTION_ROOT / "instruction-set.json").read_text(encoding="utf-8")
    )


def test_instruction_set_tracks_exact_source_hashes() -> None:
    """Bind every static instruction component to its exact repository bytes."""
    definition = _definition()
    sources = (definition.common, *definition.roles.values())

    for source in sources:
        content = (REPOSITORY_ROOT / source.relative_path).read_text(encoding="utf-8")
        assert instruction_text_sha256(content) == source.content_sha256


def test_instruction_set_defines_the_five_approved_roles() -> None:
    """Keep normal workers, primary review, and conditional audit distinct."""
    definition = _definition()

    assert tuple(definition.composition_order) == ("common", "role", "task_scoped")
    assert set(definition.roles) == {
        "orchestrator",
        "codex_worker",
        "claude_worker",
        "primary_reviewer",
        "antigravity_auditor",
    }
    assert definition.physical_distribution_implemented is False


def test_instruction_composition_is_deterministic_and_ordered() -> None:
    """Compose common, role, and validated task context in one exact order."""
    common = (INSTRUCTION_ROOT / "common.md").read_text(encoding="utf-8")
    role = (INSTRUCTION_ROOT / "codex-worker.md").read_text(encoding="utf-8")
    task_scoped = '{"schema_id":"detailed-analysis.research-context","schema_version":1}\n'

    first = compose_instruction_text(common, role, task_scoped)
    second = compose_instruction_text(common, role, task_scoped)

    assert first == second
    assert first.index("# Detailed Analysis Common Instructions") < first.index("# Codex Worker Instructions")
    assert first.endswith(task_scoped)
    assert instruction_text_sha256(first) == instruction_text_sha256(second)


def test_common_and_auditor_sources_preserve_safety_boundaries() -> None:
    """Make the shared and conditional-audit boundaries reviewable as source text."""
    common = (INSTRUCTION_ROOT / "common.md").read_text(encoding="utf-8")
    auditor = (INSTRUCTION_ROOT / "antigravity-auditor.md").read_text(encoding="utf-8")

    assert "untrusted data" in common
    assert "Do not connect to a brokerage" in common
    assert "read-only third-party auditor" in auditor
    assert "not a normal worker" in auditor
