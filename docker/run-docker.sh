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
  Set ENABLE_EDINET_CREDENTIAL=1 and HOST_EDINET_API_KEY_FILE to opt in
  to the single-file, read-only EDINET credential mount.

Examples:
  ./docker/run-docker.sh
  ./docker/run-docker.sh pytest -q
  ENABLE_EDINET_CREDENTIAL=1 \
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

    # shellcheck source=docker/common.sh
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

    local -a docker_compose_cmd=(docker compose -f docker-compose.yml)

    case "${ENABLE_EDINET_CREDENTIAL:-0}" in
        0) ;;
        1)
            if [[ -z "${HOST_EDINET_API_KEY_FILE:-}" ]]; then
                echo "HOST_EDINET_API_KEY_FILE is required when ENABLE_EDINET_CREDENTIAL=1." >&2
                return 2
            fi
            docker_compose_cmd+=(-f docker-compose.edinet.yml)
            ;;
        *)
            echo "ENABLE_EDINET_CREDENTIAL must be 0 or 1." >&2
            return 2
            ;;
    esac

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
