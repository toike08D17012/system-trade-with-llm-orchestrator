#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

# shellcheck source=docker/common.sh
source common.sh

# ==== 設定（必要なら変更）====
export USER_NAME="${USER_NAME:-kujira}"
export GROUP_NAME="${GROUP_NAME:-$USER_NAME}"

GITHUB_REPOSITORY="$(get_github_repository)"
export GITHUB_REPOSITORY

REPO_NAME="$(get_repository_name)"
export REPO_NAME

REPO_ROOT_BASENAME="$(get_repo_root_basename)"
export REPO_ROOT_BASENAME

# bash の組込み $UID をそのまま使う（上書きしない）
USER_UID="${UID}"
USER_GID="$(id -g)"
USER_HOME="/home/${USER_NAME}"

export USER_UID
export USER_GID
export USER_HOME

# ==== 実行 ====
echo "Build args:"
echo "  REPO_NAME=${REPO_NAME}"
echo "  GITHUB_REPOSITORY=${GITHUB_REPOSITORY}"
echo "  REPO_ROOT_BASENAME=${REPO_ROOT_BASENAME}"
echo "  USER_NAME=${USER_NAME}"
echo "  GROUP_NAME=${GROUP_NAME}"
echo "  USER_UID=${USER_UID}"
echo "  USER_GID=${USER_GID}"
echo "  HOME=${USER_HOME}"

docker compose build
