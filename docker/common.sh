#!/usr/bin/env bash

set -euo pipefail

get_git_remote_url() {
    git config --get remote.origin.url
}

parse_github_repository() {
    local remote_url="$1"
    local repository=""

    # Remove trailing .git if present.
    remote_url="${remote_url%.git}"

    case "$remote_url" in
        https://github.com/*)
            repository="${remote_url#https://github.com/}"
            ;;
        http://github.com/*)
            repository="${remote_url#http://github.com/}"
            ;;
        git@github.com:*)
            repository="${remote_url#git@github.com:}"
            ;;
        ssh://git@github.com/*)
            repository="${remote_url#ssh://git@github.com/}"
            ;;
        *)
            echo "Unsupported Git remote URL: $remote_url" >&2
            return 1
            ;;
    esac

    if [[ -z "$repository" || "$repository" != */* ]]; then
        echo "Failed to parse GitHub repository from remote URL: $remote_url" >&2
        return 1
    fi

    # Docker/GHCR image names should be lowercase.
    printf '%s\n' "$repository" | tr '[:upper:]' '[:lower:]'
}

get_github_repository() {
    local remote_url
    remote_url="$(get_git_remote_url)"
    parse_github_repository "$remote_url"
}

get_image_name() {
    local repository
    repository="$(get_github_repository)"

    printf 'ghcr.io/%s:latest\n' "$repository"
}

get_repository_name() {
    local repository
    repository="$(get_github_repository)"

    printf '%s\n' "${repository##*/}"
}

get_repo_root_basename() {
    local script_dir
    local repo_root

    script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)" || return 1
    repo_root="$(git -C "${script_dir}" rev-parse --show-toplevel)" || return 1

    basename "${repo_root}"
}
