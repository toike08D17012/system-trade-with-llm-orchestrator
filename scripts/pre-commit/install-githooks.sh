#!/usr/bin/env bash

set -euo pipefail

main() {
    local script_dir

    script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
    cd "${script_dir}/../.."

    if ! command -v pre-commit >/dev/null 2>&1; then
        echo "Error: pre-commit is required on the host. Install it with 'uv tool install pre-commit'." >&2
        exit 1
    fi

    exec pre-commit install --install-hooks
}

main "$@"
