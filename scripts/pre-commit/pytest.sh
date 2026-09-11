#!/usr/bin/env bash

set -euo pipefail

main() {
    local -a command_args
    local hook_dir
    local return_code

    hook_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

    # Move to the project root directory.
    cd "${hook_dir}/../.."

    if command -v docker >/dev/null 2>&1; then
        command_args=(./docker/run-docker.sh pytest)
    else
        command_args=(pytest)
    fi

    set +e
    "${command_args[@]}" "$@"
    return_code=$?
    set -e

    exit "${return_code}"
}

main "$@"
