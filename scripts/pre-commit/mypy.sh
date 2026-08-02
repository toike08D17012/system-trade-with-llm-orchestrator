#!/usr/bin/env bash

set -euo pipefail

main() {
    local hook_dir

    hook_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
    cd "${hook_dir}/../.."

    if ! command -v docker >/dev/null 2>&1; then
        exec mypy "$@"
    fi

    exec ./docker/run-docker.sh mypy "$@"
}

main "$@"
