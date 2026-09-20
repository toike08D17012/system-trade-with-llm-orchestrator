#!/usr/bin/env bash
set -euo pipefail

CONTAINER_USER=${CONTAINER_USER:-kujira}
CONTAINER_GROUP=$(id -gn "${CONTAINER_USER}")
OLD_UID=$(id -u "${CONTAINER_USER}")
OLD_GID=$(id -g "${CONTAINER_USER}")

NEW_UID=${NEW_UID:-1000}
NEW_GID=${NEW_GID:-1000}

if [ -n "$NEW_UID" ] && [ "$NEW_UID" != "$OLD_UID" ]; then
    echo "🔧 Updating '${CONTAINER_USER}' user ID to match host system..."
    usermod -u "$NEW_UID" "${CONTAINER_USER}"
    find / -user "$OLD_UID" -exec chown -h "$NEW_UID" {} \; 2>/dev/null || true
    echo "✅ '${CONTAINER_USER}' user ID updated successfully."
fi

if [ -n "$NEW_GID" ] && [ "$NEW_GID" != "$OLD_GID" ]; then
    echo "🔧 Updating '${CONTAINER_USER}' group ID to match host system..."
    EXISTING_GROUP_NAME="$(getent group "$NEW_GID" | cut -d: -f1 || true)"

    # On macOS, GID 20 (the staff group) is commonly used and may already exist in Ubuntu environments.
    # This fallback is prepared to avoid errors when running docker compose.
    if [ -n "$EXISTING_GROUP_NAME" ] && [ "$EXISTING_GROUP_NAME" != "${CONTAINER_GROUP}" ]; then
        echo "ℹ️ GID '$NEW_GID' already belongs to '$EXISTING_GROUP_NAME'. Reusing this group for '${CONTAINER_USER}'."
        usermod -g "$NEW_GID" "${CONTAINER_USER}"
    else
        groupmod -g "$NEW_GID" "${CONTAINER_GROUP}"
    fi

    find / -group "$OLD_GID" -exec chown -h :"$NEW_GID" {} \; 2>/dev/null || true
    echo "✅ '${CONTAINER_USER}' group ID updated successfully."
fi

WORKSPACE="${WORK_DIR:-${HOME}/workspace}"
if [[ -f "${WORKSPACE}/pyproject.toml" ]]; then
    echo "📦 Installing project dependencies using UV..."
    # Expand the positional workspace argument in the child shell.
    # shellcheck disable=SC2016
    gosu "${CONTAINER_USER}" bash -c 'cd -- "$1" && uv sync --frozen' bash "${WORKSPACE}"
fi

if [[ -f "${WORKSPACE}/.pre-commit-config.yaml" ]]; then
    echo "🔧 Installing pre-commit hooks..."
    # Expand the positional workspace argument in the child shell.
    # shellcheck disable=SC2016
    gosu "${CONTAINER_USER}" bash -c 'cd -- "$1" && scripts/pre-commit/install-githooks.sh' bash "${WORKSPACE}"
fi

exec gosu "${CONTAINER_USER}" "$@"
