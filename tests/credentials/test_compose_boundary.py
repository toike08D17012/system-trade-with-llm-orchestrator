"""Validate the opt-in credential mount contract in Compose files."""

import typing
from pathlib import Path

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_development_app_has_no_credential_mount() -> None:
    """Keep ordinary development and CI credential-free."""
    compose = _load_compose("docker-compose.yml")
    app = compose["services"]["app"]
    rendered = yaml.safe_dump(app)

    assert "HOST_CREDENTIAL_DIR" not in rendered
    assert "/.credentials" not in rendered
    assert "EDINET_API_KEY_FILE" not in rendered


def test_development_app_exposes_codex_home_and_preserves_custom_host_state() -> None:
    """Keep Codex pointed at the mounted state directory inside the container."""
    compose = _load_compose("docker-compose.yml")
    app = compose["services"]["app"]

    assert app["environment"] == {"CODEX_HOME": "/home/${USER_NAME:-kujira}/.codex"}
    assert {
        "type": "bind",
        "source": "${HOST_CODEX_HOME:-${CODEX_HOME:-${HOME}/.codex}}",
        "target": "/home/${USER_NAME:-kujira}/.codex",
        "bind": {"create_host_path": False},
    } in app["volumes"]


def test_edinet_overlay_adds_one_read_only_file_to_the_same_app() -> None:
    """Add only the path-valued EDINET credential contract when opted in."""
    compose = _load_compose("docker-compose.edinet.yml")

    assert set(compose["services"]) == {"app"}
    app = compose["services"]["app"]
    assert app["environment"] == {"EDINET_API_KEY_FILE": "/run/secrets/edinet_api_key"}
    assert app["volumes"] == [
        {
            "type": "bind",
            "source": "${HOST_EDINET_API_KEY_FILE:?HOST_EDINET_API_KEY_FILE is required}",
            "target": "/run/secrets/edinet_api_key",
            "read_only": True,
            "bind": {"create_host_path": False},
        }
    ]


def test_run_wrapper_requires_explicit_edinet_opt_in() -> None:
    """Keep the overlay out of ordinary wrapper execution."""
    script = (REPOSITORY_ROOT / "docker" / "run-docker.sh").read_text(encoding="utf-8")

    assert 'case "${ENABLE_EDINET_CREDENTIAL:-0}"' in script
    assert "docker_compose_cmd+=(-f docker-compose.edinet.yml)" in script
    assert "HOST_EDINET_API_KEY_FILE is required when ENABLE_EDINET_CREDENTIAL=1." in script


def _load_compose(filename: str) -> dict[str, typing.Any]:
    content = (REPOSITORY_ROOT / "docker" / filename).read_text(encoding="utf-8")
    loaded = yaml.safe_load(content)
    assert isinstance(loaded, dict)
    assert all(isinstance(key, str) for key in loaded)
    return typing.cast("dict[str, typing.Any]", loaded)
