"""Offline discovery and validation for versioned application configuration."""

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from stock_research_llm_orchestrator.contracts.base import StrictContractModel
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.policies import (
    ApprovalStatus,
    SourceApprovalV1,
    SourceProfileV1,
)
from stock_research_llm_orchestrator.contracts.validation.wrapper import (
    ContractValidationError,
    InputFormat,
    validate_text,
)
from stock_research_llm_orchestrator.errors import ApplicationError, ExitCode


_VERSION_FILE = re.compile(r"^v([1-9][0-9]*)\.yaml$")
_LAYOUT_SCHEMAS = {
    "policies/detailed-analysis": "detailed-analysis.detailed-analysis-policy",
    "policies/web-research": "detailed-analysis.web-research-policy",
    "policies/review-audit": "detailed-analysis.review-audit-policy",
    "policies/session-continuation": "detailed-analysis.session-continuation-policy",
    "market-profiles": "detailed-analysis.market-profile",
    "source-approvals": "detailed-analysis.source-approval",
    "source-profiles": "detailed-analysis.source-profile",
}


@dataclass(frozen=True)
class ConfigurationArtifact:
    """A validated configuration artifact and its raw-file identity."""

    path: Path
    model: StrictContractModel
    sha256: str


def validate_configuration(config_dir: Path) -> tuple[ConfigurationArtifact, ...]:
    """Validate all supported versioned YAML configuration under a root."""
    root = _resolve_root(config_dir)
    paths = _discover_paths(root)
    artifacts = tuple(_load_artifact(root, path) for path in paths)
    _validate_inventory(artifacts)
    return artifacts


def _resolve_root(config_dir: Path) -> Path:
    try:
        root = config_dir.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ApplicationError(
            "Configuration root does not exist or is not accessible.", ExitCode.CONFIGURATION_IO
        ) from exc
    if not root.is_dir():
        raise ApplicationError("Configuration root is not a directory.", ExitCode.CONFIGURATION_IO)
    return root


def _discover_paths(root: Path) -> tuple[Path, ...]:
    try:
        paths = tuple(sorted(root.rglob("*.yaml"), key=lambda path: path.relative_to(root).as_posix()))
    except OSError as exc:
        raise ApplicationError("Configuration files could not be enumerated.", ExitCode.CONFIGURATION_IO) from exc
    if not paths:
        raise ApplicationError("Configuration root contains no YAML artifacts.", ExitCode.INVALID_CONFIGURATION)
    return paths


def _load_artifact(root: Path, path: Path) -> ConfigurationArtifact:
    relative = path.relative_to(root)
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ApplicationError(
            "A configuration file does not exist or is not accessible.", ExitCode.CONFIGURATION_IO
        ) from exc
    if not resolved.is_relative_to(root):
        raise ApplicationError("A configuration path escapes the configuration root.", ExitCode.CONFIGURATION_IO)
    if not resolved.is_file():
        raise ApplicationError("A configuration path is not a readable file.", ExitCode.CONFIGURATION_IO)
    try:
        raw = resolved.read_bytes()
        text = raw.decode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise ApplicationError("A configuration file could not be read as UTF-8.", ExitCode.CONFIGURATION_IO) from exc
    try:
        model = validate_text(text, InputFormat.YAML)
    except ContractValidationError as exc:
        raise ApplicationError("Configuration artifact validation failed.", ExitCode.INVALID_CONFIGURATION) from exc
    _validate_layout(relative, model)
    return ConfigurationArtifact(path=relative, model=model, sha256=hashlib.sha256(raw).hexdigest())


def _validate_layout(relative: Path, model: StrictContractModel) -> None:
    parts = relative.parts
    version_match = _VERSION_FILE.fullmatch(relative.name)
    expected_schema: str | None = None
    identity: str | None = None
    if len(parts) == 3 and parts[0] == "policies":
        expected_schema = _LAYOUT_SCHEMAS.get(f"policies/{parts[1]}")
    elif len(parts) == 3 and parts[0] in {"market-profiles", "source-approvals", "source-profiles"}:
        expected_schema = _LAYOUT_SCHEMAS[parts[0]]
        identity = parts[1]
    if expected_schema is None or version_match is None:
        raise ApplicationError("Configuration artifact uses an unsupported layout.", ExitCode.INVALID_CONFIGURATION)
    version = int(version_match.group(1))
    schema_id, schema_version = _schema_identity(model)
    if schema_id != expected_schema or schema_version != version:
        raise ApplicationError(
            "Configuration layout does not match its schema identity.", ExitCode.INVALID_CONFIGURATION
        )
    if identity is not None:
        field_name = "mic" if parts[0] == "market-profiles" else "source_id"
        field_value = getattr(model, field_name, None)
        if not isinstance(field_value, str) or field_value.lower() != identity.lower():
            raise ApplicationError(
                "Configuration layout does not match its artifact identity.", ExitCode.INVALID_CONFIGURATION
            )


def _validate_inventory(artifacts: tuple[ConfigurationArtifact, ...]) -> None:
    keys: set[tuple[str, int, str]] = set()
    artifact_ids: set[str] = set()
    approvals: dict[str, ConfigurationArtifact] = {}
    profiles: list[ConfigurationArtifact] = []
    for artifact in artifacts:
        artifact_id = getattr(artifact.model, "artifact_id", None)
        if not isinstance(artifact_id, str):
            raise ApplicationError("Configuration artifact identity is missing.", ExitCode.INVALID_CONFIGURATION)
        schema_id, schema_version = _schema_identity(artifact.model)
        key = (schema_id, schema_version, artifact_id)
        if key in keys or artifact_id in artifact_ids:
            raise ApplicationError(
                "Configuration artifacts contain a duplicate logical identity.", ExitCode.INVALID_CONFIGURATION
            )
        keys.add(key)
        artifact_ids.add(artifact_id)
        if isinstance(artifact.model, SourceApprovalV1):
            approvals[artifact_id] = artifact
        elif isinstance(artifact.model, SourceProfileV1):
            profiles.append(artifact)
    for profile_artifact in profiles:
        _validate_source_reference(profile_artifact, approvals)


def _validate_source_reference(
    profile_artifact: ConfigurationArtifact, approvals: dict[str, ConfigurationArtifact]
) -> None:
    profile = profile_artifact.model
    assert isinstance(profile, SourceProfileV1)
    reference = profile.source_approval_reference
    approval_artifact = approvals.get(reference.artifact_id)
    if approval_artifact is None:
        raise ApplicationError("A source profile references a missing source approval.", ExitCode.INVALID_CONFIGURATION)
    approval = approval_artifact.model
    assert isinstance(approval, SourceApprovalV1)
    consistent = (
        reference.artifact_type == "source_approval"
        and reference.schema_id == approval.schema_id
        and reference.schema_version == approval.schema_version
        and reference.sha256 == approval_artifact.sha256
        and profile.source_id == approval.source_id
        and profile.source_approval_status is approval.status
        and profile.credential_scope_alias == approval.credential_scope_alias
        and (not profile.enabled or (approval.status is ApprovalStatus.APPROVED and approval.online_use_allowed))
    )
    if not consistent:
        raise ApplicationError(
            "A source profile is inconsistent with its source approval.", ExitCode.INVALID_CONFIGURATION
        )


def _schema_identity(model: StrictContractModel) -> tuple[str, int]:
    schema_id = getattr(model, "schema_id", None)
    schema_version = getattr(model, "schema_version", None)
    if not isinstance(schema_id, str) or not isinstance(schema_version, int):
        raise ApplicationError("Configuration schema identity is missing.", ExitCode.INVALID_CONFIGURATION)
    return schema_id, schema_version
