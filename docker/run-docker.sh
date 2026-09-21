#!/usr/bin/env bash

set -euo pipefail

usage() {
    cat <<'EOF'
Usage:
  ./docker/run-docker.sh [COMMAND ...]
  ./docker/run-docker.sh -h | --help

Description:
  Runs the app development service with the host user's UID/GID.
  Pulls the image first and builds locally if the pull fails.
  Mounts the EDINET API key read-only from
  $HOME/.config/system-trade-with-llm-orchestrator/edinet-api-key.
  Set HOST_EDINET_API_KEY_FILE to override the host path.

Examples:
  ./docker/run-docker.sh
  ./docker/run-docker.sh pytest -q
  HOST_EDINET_API_KEY_FILE=/path/outside/repository/edinet-api-key \
    ./docker/run-docker.sh COMMAND
EOF
}

main() {
    cd "$(dirname "$0")" || exit 1

    if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
        usage
        exit 0
    fi

    # shellcheck disable=SC1091
    source common.sh

    local service_name="app"

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

    local credential_file="${HOST_EDINET_API_KEY_FILE:-${HOME}/.config/system-trade-with-llm-orchestrator/edinet-api-key}"
    if [[ ! -f "${credential_file}" ]]; then
        echo "EDINET API key file is required at HOST_EDINET_API_KEY_FILE or the default host path." >&2
        return 2
    fi
    HOST_EDINET_API_KEY_FILE="${credential_file}"
    export HOST_EDINET_API_KEY_FILE

    local -a docker_compose_cmd=(docker compose -f docker-compose.yml)

    if ! "${docker_compose_cmd[@]}" pull; then
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
