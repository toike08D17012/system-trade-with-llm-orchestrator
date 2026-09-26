"""Tests for offline versioned configuration validation."""

import shutil
from pathlib import Path

import pytest

from stock_research_llm_orchestrator.configuration import validate_configuration
from stock_research_llm_orchestrator.errors import ApplicationError, ExitCode


REPOSITORY_CONFIG = Path(__file__).parents[1] / "config"


def _copy_config(tmp_path: Path) -> Path:
    destination = tmp_path / "config"
    shutil.copytree(REPOSITORY_CONFIG, destination)
    return destination


def test_validate_configuration_accepts_repository_config_in_path_order(tmp_path: Path) -> None:
    """Validate the repository configuration tree in deterministic path order."""
    root = _copy_config(tmp_path)
    artifacts = validate_configuration(root)
    paths = [artifact.path.as_posix() for artifact in artifacts]
    assert len(paths) == 19
    assert "source-approvals/boj/v1.yaml" in paths
    assert "source-profiles/boj/v1.yaml" in paths
    assert "source-approvals/boj/v2.yaml" in paths
    assert "source-profiles/boj/v2.yaml" in paths
    assert "source-approvals/jpx/v1.yaml" in paths
    assert "source-profiles/jpx/v1.yaml" in paths
    assert "source-approvals/edinet/v1.yaml" in paths
    assert "source-profiles/edinet/v1.yaml" in paths
    assert "source-approvals/yfinance/v2.yaml" in paths
    assert "source-approvals/yfinance/v3.yaml" in paths
    assert "source-profiles/yfinance/v3.yaml" in paths
    assert "source-profiles/yfinance/v2.yaml" in paths
    assert paths == sorted(paths)


@pytest.mark.parametrize("root_state", ["missing", "file"])
def test_validate_configuration_rejects_invalid_root(tmp_path: Path, root_state: str) -> None:
    """Classify missing and non-directory roots as filesystem failures."""
    root = tmp_path / "config"
    if root_state == "file":
        root.write_text("not a directory", encoding="utf-8")
    with pytest.raises(ApplicationError) as exc_info:
        validate_configuration(root)
    assert exc_info.value.exit_code == ExitCode.CONFIGURATION_IO


def test_validate_configuration_rejects_empty_root(tmp_path: Path) -> None:
    """Classify an empty readable root as invalid configuration content."""
    root = tmp_path / "config"
    root.mkdir()
    with pytest.raises(ApplicationError) as exc_info:
        validate_configuration(root)
    assert exc_info.value.exit_code == ExitCode.INVALID_CONFIGURATION


def test_validate_configuration_rejects_outside_root_symlink(tmp_path: Path) -> None:
    """Reject a discovered YAML symlink that resolves outside the root."""
    root = tmp_path / "config"
    target = tmp_path / "outside.yaml"
    target.write_text("schema_id: secret\n", encoding="utf-8")
    link = root / "policies" / "detailed-analysis" / "v1.yaml"
    link.parent.mkdir(parents=True)
    link.symlink_to(target)
    with pytest.raises(ApplicationError) as exc_info:
        validate_configuration(root)
    assert exc_info.value.exit_code == ExitCode.CONFIGURATION_IO


def test_validate_configuration_rejects_unsafe_yaml_without_exposing_value(tmp_path: Path) -> None:
    """Map unsafe YAML to a sanitized configuration-content failure."""
    root = tmp_path / "config"
    path = root / "policies" / "detailed-analysis" / "v1.yaml"
    path.parent.mkdir(parents=True)
    path.write_text("secret: &s sensitive-value\ncopy: *s\n", encoding="utf-8")
    with pytest.raises(ApplicationError) as exc_info:
        validate_configuration(root)
    assert exc_info.value.exit_code == ExitCode.INVALID_CONFIGURATION
    assert "sensitive-value" not in exc_info.value.message


def test_validate_configuration_rejects_layout_schema_mismatch(tmp_path: Path) -> None:
    """Require a supported schema to appear only in its owned layout."""
    root = _copy_config(tmp_path)
    source = root / "market-profiles" / "xtks" / "v1.yaml"
    destination = root / "policies" / "detailed-analysis" / "v1.yaml"
    destination.write_bytes(source.read_bytes())
    with pytest.raises(ApplicationError) as exc_info:
        validate_configuration(root)
    assert exc_info.value.exit_code == ExitCode.INVALID_CONFIGURATION


def test_validate_configuration_rejects_source_artifact_version_mismatch(tmp_path: Path) -> None:
    """Bind source filenames to artifact revisions while keeping schema v1."""
    root = _copy_config(tmp_path)
    source = root / "source-profiles" / "yfinance" / "v2.yaml"
    destination = root / "source-profiles" / "yfinance" / "v3.yaml"
    source.rename(destination)
    with pytest.raises(ApplicationError) as exc_info:
        validate_configuration(root)

    assert exc_info.value.exit_code == ExitCode.INVALID_CONFIGURATION


def test_validate_configuration_rejects_missing_approval(tmp_path: Path) -> None:
    """Reject a source profile whose referenced approval is absent."""
    root = _copy_config(tmp_path)
    (root / "source-approvals" / "yfinance" / "v1.yaml").unlink()
    with pytest.raises(ApplicationError) as exc_info:
        validate_configuration(root)
    assert exc_info.value.exit_code == ExitCode.INVALID_CONFIGURATION


def test_validate_configuration_rejects_source_approval_hash_mismatch(tmp_path: Path) -> None:
    """Compare a profile reference against the raw approval-file digest."""
    root = _copy_config(tmp_path)
    approval = root / "source-approvals" / "yfinance" / "v1.yaml"
    approval.write_text(approval.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ApplicationError) as exc_info:
        validate_configuration(root)
    assert exc_info.value.exit_code == ExitCode.INVALID_CONFIGURATION


def test_validate_configuration_rejects_source_approval_metadata_mismatch(tmp_path: Path) -> None:
    """Cross-check approval status and credential scope beyond model-local rules."""
    root = _copy_config(tmp_path)
    profile = root / "source-profiles" / "yfinance" / "v1.yaml"
    text = profile.read_text(encoding="utf-8")
    text = text.replace("source_approval_status: approved", "source_approval_status: draft")
    text = text.replace("enabled: true", "enabled: false")
    profile.write_text(text, encoding="utf-8")
    with pytest.raises(ApplicationError) as exc_info:
        validate_configuration(root)
    assert exc_info.value.exit_code == ExitCode.INVALID_CONFIGURATION


def test_validate_configuration_rejects_duplicate_artifact_id(tmp_path: Path) -> None:
    """Reject a logical artifact identity reused by another configuration."""
    root = _copy_config(tmp_path)
    web_policy = root / "policies" / "web-research" / "v1.yaml"
    text = web_policy.read_text(encoding="utf-8")
    web_policy.write_text(
        text.replace("artifact_id: web-research-policy-v1", "artifact_id: detailed-analysis-policy-v1"),
        encoding="utf-8",
    )
    with pytest.raises(ApplicationError) as exc_info:
        validate_configuration(root)
    assert exc_info.value.exit_code == ExitCode.INVALID_CONFIGURATION
