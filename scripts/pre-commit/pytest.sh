#!/usr/bin/env bash

set -euo pipefail

main() {
    local hook_dir
    local return_code

    hook_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

    # Move to the project root directory.
    cd "${hook_dir}/../.."

    if ! command -v docker >/dev/null 2>&1; then
        exec pytest "$@"
    fi

    set +e
    ./docker/run-docker.sh pytest "$@"
    return_code=$?
    set -e

    if [[ "${return_code}" -eq 5 ]]; then
        echo "No tests collected. Treating pytest exit code 5 as success."
        exit 0
    fi

    exit "${return_code}"
}

main "$@"
