#!/usr/bin/env bash
set -euo pipefail

HOOK_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

# Move to the project root directory
cd "${HOOK_DIR}/.."

if command -v pre-commit >/dev/null 2>&1; then
    pre-commit install --install-hooks
else
    echo "pre-commit is not installed. Please install by running 'pip install pre-commit' or using Docker." >&2
fi
