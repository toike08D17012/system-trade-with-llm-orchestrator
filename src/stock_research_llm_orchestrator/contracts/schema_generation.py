"""Deterministic JSON Schema generation for public contracts."""

import argparse
import json
from pathlib import Path

from stock_research_llm_orchestrator.contracts.registry import ContractRegistry, default_registry


JSON_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"


def schema_relative_path(schema_id: str, schema_version: int) -> Path:
    """Return the approved output path for one public schema."""
    prefix = "detailed-analysis."
    if not schema_id.startswith(prefix):
        raise ValueError("schema_id is outside the detailed-analysis namespace")
    artifact_name = schema_id.removeprefix(prefix)
    return Path("schemas") / "detailed-analysis" / f"v{schema_version}" / f"{artifact_name}.schema.json"


def generate_schema_documents(registry: ContractRegistry = default_registry) -> dict[Path, str]:
    """Generate stable schema documents keyed by repository-relative path."""
    documents: dict[Path, str] = {}
    for (schema_id, schema_version), model in registry.items():
        schema = model.model_json_schema(mode="validation")
        schema["$schema"] = JSON_SCHEMA_DIALECT
        schema["$id"] = f"urn:stock-research-llm-orchestrator:{schema_id}:v{schema_version}"
        path = schema_relative_path(schema_id, schema_version)
        documents[path] = json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    return documents


def synchronize_schemas(repository_root: Path, *, check: bool) -> list[Path]:
    """Write schemas or return paths that differ from generated content."""
    mismatches: list[Path] = []
    for relative_path, content in generate_schema_documents().items():
        output_path = repository_root / relative_path
        if check:
            if not output_path.is_file() or output_path.read_text(encoding="utf-8") != content:
                mismatches.append(relative_path)
            continue
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content, encoding="utf-8", newline="\n")
    return mismatches


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[3]


def main() -> int:
    """Generate schemas or verify that tracked schemas are current."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail when generated schemas differ")
    parser.add_argument("--repository-root", type=Path, default=_repository_root())
    args = parser.parse_args()
    mismatches = synchronize_schemas(args.repository_root, check=args.check)
    if mismatches:
        for path in mismatches:
            print(path.as_posix())
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
