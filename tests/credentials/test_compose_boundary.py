"""Validate the default credential mount contract in Compose files."""

import typing
from pathlib import Path

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_development_app_mounts_edinet_credential_read_only() -> None:
    """Mount only the path-valued EDINET credential by default."""
    compose = _load_compose("docker-compose.yml")
    app = compose["services"]["app"]
    rendered = yaml.safe_dump(app)

    assert "HOST_CREDENTIAL_DIR" not in rendered
    assert "/.credentials" not in rendered
    assert app["environment"]["EDINET_API_KEY_FILE"] == "/run/secrets/edinet_api_key"
    assert {
        "type": "bind",
        "source": "${HOST_EDINET_API_KEY_FILE:-${HOME}/.config/system-trade-with-llm-orchestrator/edinet-api-key}",
        "target": "/run/secrets/edinet_api_key",
        "read_only": True,
        "bind": {"create_host_path": False},
    } in app["volumes"]


def test_development_app_exposes_codex_home_and_preserves_custom_host_state() -> None:
    """Keep Codex pointed at the mounted state directory inside the container."""
    compose = _load_compose("docker-compose.yml")
    app = compose["services"]["app"]

    assert app["environment"] == {
        "CODEX_HOME": "/home/${USER_NAME:-kujira}/.codex",
        "EDINET_API_KEY_FILE": "/run/secrets/edinet_api_key",
    }
    assert {
        "type": "bind",
        "source": "${HOST_CODEX_HOME:-${CODEX_HOME:-${HOME}/.codex}}",
        "target": "/home/${USER_NAME:-kujira}/.codex",
        "bind": {"create_host_path": False},
    } in app["volumes"]


def test_run_wrapper_requires_the_default_edinet_key_file() -> None:
    """Require the default mount source before invoking Compose."""
    script = (REPOSITORY_ROOT / "docker" / "run-docker.sh").read_text(encoding="utf-8")

    assert "ENABLE_EDINET_CREDENTIAL" not in script
    assert "docker-compose.edinet.yml" not in script
    assert "HOST_EDINET_API_KEY_FILE:-${HOME}/.config/system-trade-with-llm-orchestrator/edinet-api-key" in script
    assert "EDINET API key file is required at HOST_EDINET_API_KEY_FILE or the default host path." in script


def test_devcontainer_uses_compose_with_the_default_edinet_mount() -> None:
    """Keep the devcontainer on the base Compose credential contract."""
    devcontainer = (REPOSITORY_ROOT / ".devcontainer" / "devcontainer.json").read_text(encoding="utf-8")

    assert '"../docker/docker-compose.yml"' in devcontainer
    assert "docker-compose.edinet.yml" not in devcontainer


def _load_compose(filename: str) -> dict[str, typing.Any]:
    content = (REPOSITORY_ROOT / "docker" / filename).read_text(encoding="utf-8")
    loaded = yaml.safe_load(content)
    assert isinstance(loaded, dict)
    assert all(isinstance(key, str) for key in loaded)
    return typing.cast("dict[str, typing.Any]", loaded)
