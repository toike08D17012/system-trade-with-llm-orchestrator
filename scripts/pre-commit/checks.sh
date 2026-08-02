#!/usr/bin/env bash

set -euo pipefail

main() {
    local hook_dir

    hook_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
    # Change to the root of the repository
    cd "${hook_dir}/../.."

    # Run all checks in docker container using pre-commit
    if ! command -v docker >/dev/null 2>&1; then
        exec pre-commit run --all-files
    else
        exec ./docker/run-docker.sh pre-commit run --all-files
    fi
}

main "$@"
