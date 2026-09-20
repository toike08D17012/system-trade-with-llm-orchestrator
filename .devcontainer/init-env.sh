#!/usr/bin/env bash

set -euo pipefail

main() {
    local generated_file="docker/.env"

    # Move to the repository root
    cd "$(dirname "$0")/.." || exit 1

    source ./docker/common.sh

    prepare_agent_state_paths

    REPO_NAME="$(get_repository_name)"
    GITHUB_REPOSITORY="$(get_github_repository)"
    REPO_ROOT_BASENAME="$(get_repo_root_basename)"

    cat >"${generated_file}" <<EOF
REPO_NAME=${REPO_NAME}
GITHUB_REPOSITORY=${GITHUB_REPOSITORY}
REPO_ROOT_BASENAME=${REPO_ROOT_BASENAME}
EOF

}

main "$@"
