#!/usr/bin/env bash

set -euo pipefail

main() {
    local script_dir

    script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
    cd "${script_dir}/../.."

    ruff format --check .
    ruff check .
    mypy src
    pytest
    python -m stock_research_llm_orchestrator.contracts.schema_generation --check
    ./scripts/pre-commit/shell-check.sh
    pre-commit validate-config .pre-commit-config.yaml
    git diff --exit-code
    test -z "$(git status --porcelain)"
}

main "$@"
