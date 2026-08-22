#!/usr/bin/env bash

set -euo pipefail

usage() {
    cat <<'EOF'
Usage:
  ./docker/run-docker.sh [COMMAND ...]
  ./docker/run-docker.sh -h | --help

Description:
  Runs the project container with automatic CPU/GPU switching.
  If NVIDIA GPU is available, it runs app-gpu with --profile gpu.
  Otherwise, it runs app.

Examples:
  ./docker/run-docker.sh
  ./docker/run-docker.sh pytest -q
EOF
}

prepare_agent_state_paths() {
    local -a required_directories=(
        "${HOST_CODEX_HOME:-${HOME}/.codex}"
        "${HOST_CLAUDE_HOME:-${HOME}/.claude}"
        "${HOST_ANTIGRAVITY_HOME:-${HOME}/.gemini}"
    )
    local claude_state_file="${HOST_CLAUDE_STATE_FILE:-${HOME}/.claude.json}"
    local path

    for path in "${required_directories[@]}"; do
        if [[ -e "${path}" && ! -d "${path}" ]]; then
            echo "Agent state path is not a directory: ${path}" >&2
            return 1
        fi

        mkdir -p -- "${path}"
    done

    if [[ -e "${claude_state_file}" && ! -f "${claude_state_file}" ]]; then
        echo "Claude state path is not a file: ${claude_state_file}" >&2
        return 1
    fi

    if [[ ! -e "${claude_state_file}" ]]; then
        printf '{}\n' >"${claude_state_file}"
    fi
}

main() {
    cd "$(dirname "$0")" || exit 1

    if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
        usage
        exit 0
    fi

    # shellcheck source=docker/common.sh
    source common.sh

    local service_name="app"
    local -a profile_args=()

    GITHUB_REPOSITORY="$(get_github_repository)"
    export GITHUB_REPOSITORY

    REPO_NAME="$(get_repository_name)"
    export REPO_NAME

    REPO_ROOT_BASENAME="$(get_repo_root_basename)"
    export REPO_ROOT_BASENAME

    prepare_agent_state_paths

    # if bash_history does not exist,
    # create an empty file to avoid creating it as directory
    if [[ ! -f .bash_history ]]; then
        touch .bash_history
    fi

    local -a command=("$@")
    if ((${#command[@]} == 0)); then
        command=(bash)
    fi

    local -a docker_compose_cmd=(docker compose)
    if ((${#profile_args[@]} > 0)); then
        docker_compose_cmd+=("${profile_args[@]}")
    fi

    if ! docker compose pull; then
        echo "Remote image pull failed; building locally." >&2
        ./build-docker.sh
    fi

    docker_compose_cmd+=(
        run
        --rm
        -e "NEW_UID=$(id -u)"
        -e "NEW_GID=$(id -g)"
        "${service_name}"
        "${command[@]}"
    )

    "${docker_compose_cmd[@]}"
}

main "$@"
