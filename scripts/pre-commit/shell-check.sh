#!/usr/bin/env bash

set -euo pipefail

collect_shell_files() {
    if [ "$#" -gt 0 ]; then
        printf '%s\0' "$@"
        return
    fi

    find . \
        -type f \
        \( -name "*.sh" -o -name "*.bash" \) \
        -not -path "./.git/*" \
        -not -path "./.venv/*" \
        -not -path "./node_modules/*" \
        -not -path "./dist/*" \
        -not -path "./build/*" \
        -print0
}

run_shell_check() {
    local -a files=()

    if ! command -v shfmt >/dev/null 2>&1; then
        echo "Error: shfmt is not installed." >&2
        exit 1
    fi

    if ! command -v shellcheck >/dev/null 2>&1; then
        echo "Error: shellcheck is not installed." >&2
        exit 1
    fi

    if [ "$#" -gt 0 ]; then
        shellcheck "$@"
        shfmt -d "$@"
        return
    fi

    while IFS= read -r -d '' file; do
        files+=("${file}")
    done < <(collect_shell_files "$@")

    if [ "${#files[@]}" -eq 0 ]; then
        echo "No shell scripts found."
        return
    fi

    shfmt -d "${files[@]}"
    shellcheck "${files[@]}"
}

main() {
    local hook_dir

    hook_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
    # Move to the project root directory.
    cd "${hook_dir}/../.."

    if ! command -v docker >/dev/null 2>&1; then
        run_shell_check "$@"
        return
    fi

    exec ./docker/run-docker.sh ./scripts/pre-commit/shell-check.sh "$@"
}

main "$@"
