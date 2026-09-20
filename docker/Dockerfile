# syntax=docker/dockerfile:1

# ----------------------------------------------------------------------
# markdownlint-cli2 builder
# ----------------------------------------------------------------------
FROM node:22-bookworm-slim AS markdownlint-builder

ARG MARKDOWNLINT_CLI2_VERSION=0.23.2

RUN npm install --global "markdownlint-cli2@${MARKDOWNLINT_CLI2_VERSION}" \
    && npm cache clean --force


# ======================================================================
# Runtime
# ======================================================================
FROM debian:bookworm-slim

ARG USER_UID=1000
ARG USER_GID=1000
ARG REPO_NAME

ARG DEBIAN_FRONTEND=noninteractive
ARG SHELLCHECK_VERSION=0.9.0-1
ARG SHFMT_VERSION=3.6.0-1+b2

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# ----------------------------------------------------------------------
# Common development tools
# ----------------------------------------------------------------------
RUN rm -f /etc/apt/apt.conf.d/docker-clean \
    && echo 'Binary::apt::APT::Keep-Downloaded-Packages "true";' \
        > /etc/apt/apt.conf.d/keep-cache

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        git \
        gosu \
        ripgrep \
        jq \
        less \
        unzip \
        file \
        procps \
        sudo \
        "shellcheck=${SHELLCHECK_VERSION}" \
        "shfmt=${SHFMT_VERSION}" \
        libatomic1 \
        libstdc++6

# ----------------------------------------------------------------------
# markdownlint-cli2
# ----------------------------------------------------------------------
COPY --from=markdownlint-builder \
    /usr/local/bin/node \
    /usr/local/bin/node

COPY --from=markdownlint-builder \
    /usr/local/lib/node_modules/markdownlint-cli2 \
    /usr/local/lib/node_modules/markdownlint-cli2

RUN ln -s \
    /usr/local/lib/node_modules/markdownlint-cli2/markdownlint-cli2-bin.mjs \
    /usr/local/bin/markdownlint-cli2

# ----------------------------------------------------------------------
# Non-root user
# ----------------------------------------------------------------------
ARG USER_UID
ARG USER_GID
ARG USER_NAME=kujira
ARG GROUP_NAME=${USER_NAME}

ENV HOME=/home/${USER_NAME} \
    CONTAINER_USER=${USER_NAME}

RUN userdel -r ubuntu 2>/dev/null || true \
    && groupadd --gid ${USER_GID} --non-unique ${GROUP_NAME} \
    && useradd -m --no-log-init --uid ${USER_UID} --gid ${USER_GID} --groups sudo --base-dir /home --shell /bin/bash ${USER_NAME} \
    && touch ${HOME}/.sudo_as_admin_successful \
    && printf '%s\n' '%sudo ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/90-sudo-group-nopasswd \
    && chmod 0440 /etc/sudoers.d/90-sudo-group-nopasswd \
    && visudo -cf /etc/sudoers.d/90-sudo-group-nopasswd

USER ${USER_NAME}

ENV PATH="${HOME}/.local/bin:${PATH}"

# ----------------------------------------------------------------------
# Coding Agents
# ----------------------------------------------------------------------
COPY --chown=${USER_UID}:${USER_GID} --chmod=755 \
    scripts/install-codex.sh \
    scripts/install-claude-code.sh \
    scripts/install-antigravity-cli.sh \
    /tmp/coding-agent-installers/

RUN /tmp/coding-agent-installers/install-codex.sh

# Keep the image-managed Codex package separate from bind-mounted user state.
USER root

RUN codex_binary="$(readlink -f "${HOME}/.local/bin/codex")" \
    && codex_release_dir="$(dirname "$(dirname "${codex_binary}")")" \
    && mv "${codex_release_dir}" /opt/codex \
    && ln -s /opt/codex/bin/codex /usr/local/bin/codex \
    && rm "${HOME}/.local/bin/codex" \
    && rm -r "${HOME}/.codex"

USER ${USER_NAME}

RUN /tmp/coding-agent-installers/install-claude-code.sh

RUN /tmp/coding-agent-installers/install-antigravity-cli.sh \
    --dir "${HOME}/.local/bin" \
    && rm -rf /tmp/coding-agent-installers

# ----------------------------------------------------------------------
# Python environment
# ----------------------------------------------------------------------
ENV WORK_DIR=${HOME}/workspace/${REPO_NAME} \
    PYTHON_VERSION=3.14.2 \
    UV_PROJECT_ENVIRONMENT=${HOME}/.venv \
    UV_LINK_MODE=copy

WORKDIR ${WORK_DIR}

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV PATH="${UV_PROJECT_ENVIRONMENT}/bin:${HOME}/.local/bin:${PATH}"

COPY --chown=${USER_UID}:${USER_GID} \
    pyproject.toml uv.lock README.md ./

RUN --mount=type=cache,target=${HOME}/.cache/uv,uid=${USER_UID},gid=${USER_GID},mode=0755 \
    uv python install ${PYTHON_VERSION} \
    && uv sync --frozen --no-install-project

CMD ["/bin/bash"]

USER root
COPY --chmod=755 docker/entrypoint.sh /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
