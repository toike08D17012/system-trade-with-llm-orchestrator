"""Safe parsing and exact Pydantic contract dispatch."""

import json
import math
from collections.abc import Callable
from enum import StrEnum
from typing import cast

import yaml  # type: ignore[import-untyped]
from pydantic import JsonValue, ValidationError
from yaml.tokens import AliasToken, AnchorToken, TagToken  # type: ignore[import-untyped]

from stock_research_llm_orchestrator.contracts.base import StrictContractModel
from stock_research_llm_orchestrator.contracts.errors import (
    ErrorCategory,
    ErrorCode,
    ValidationErrorV1,
    ValidationTargetV1,
)
from stock_research_llm_orchestrator.contracts.registry import (
    ContractRegistry,
    UnsupportedContractError,
    default_registry,
)
from stock_research_llm_orchestrator.contracts.validation.issues import RuleValidationError


MAX_INPUT_BYTES = 1_000_000
MAX_VALUE_DEPTH = 64
type ArtifactValidator = Callable[[StrictContractModel], None]


class InputFormat(StrEnum):
    """Supported text input formats."""

    JSON = "json"
    YAML = "yaml"


class ContractValidationError(ValueError):
    """Expose a stable error artifact instead of a library exception."""

    def __init__(self, artifact: ValidationErrorV1) -> None:
        """Store the public error artifact without exposing rejected values."""
        super().__init__(artifact.message)
        self.artifact = artifact


class _DuplicateKeyError(ValueError):
    pass


class _InvalidJsonValueError(ValueError):
    pass


class _UniqueKeySafeLoader(yaml.SafeLoader):  # type: ignore[misc]
    def construct_mapping(self, node: object, deep: bool = False) -> dict[object, object]:
        pairs = self.construct_pairs(node, deep=deep)  # type: ignore[arg-type, no-untyped-call]
        mapping: dict[object, object] = {}
        for key, value in pairs:
            if key in mapping:
                raise _DuplicateKeyError("duplicate YAML mapping key")
            mapping[key] = value
        return mapping


def validate_text(
    text: str,
    input_format: InputFormat,
    *,
    registry: ContractRegistry = default_registry,
    validators: tuple[ArtifactValidator, ...] = (),
) -> StrictContractModel:
    """Parse, dispatch, and validate one machine-readable contract.

    Args:
        text: Untrusted JSON or YAML text.
        input_format: Declared input syntax; no format guessing is performed.
        registry: Exact-version model registry.
        validators: Dedicated cross-artifact validators to run after Pydantic.

    Returns:
        A validated immutable contract model.

    Raises:
        ContractValidationError: If parsing or any validation stage fails.
    """
    target = ValidationTargetV1(
        artifact_type="unknown",
        schema_id="unknown",
        schema_version=1,
    )
    try:
        value = _parse_text(text, input_format)
    except _DuplicateKeyError as exc:
        raise _failure(ErrorCategory.SCHEMA, ErrorCode.DUPLICATE_YAML_KEY, str(exc), target) from None
    except _InvalidJsonValueError as exc:
        raise _failure(ErrorCategory.SCHEMA, ErrorCode.INVALID_JSON, str(exc), target) from None
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise _failure(ErrorCategory.SCHEMA, ErrorCode.INVALID_JSON, "input is not valid JSON", target) from exc
    except yaml.YAMLError as exc:
        raise _failure(ErrorCategory.SCHEMA, ErrorCode.UNSAFE_YAML, "input is not safe YAML", target) from exc
    except ValueError as exc:
        raise _failure(ErrorCategory.SCHEMA, ErrorCode.NON_JSON_YAML_VALUE, str(exc), target) from None

    if not isinstance(value, dict):
        raise _failure(
            ErrorCategory.SCHEMA,
            ErrorCode.TYPE_MISMATCH,
            "top-level contract value must be an object",
            target,
        )
    schema_id = value.get("schema_id")
    schema_version = value.get("schema_version")
    if not isinstance(schema_id, str) or not schema_id:
        raise _failure(
            ErrorCategory.SCHEMA,
            ErrorCode.REQUIRED_FIELD_MISSING,
            "schema_id must be a non-empty string",
            target,
            instance_path="/schema_id",
        )
    if isinstance(schema_version, bool) or not isinstance(schema_version, int):
        raise _failure(
            ErrorCategory.SCHEMA,
            ErrorCode.TYPE_MISMATCH,
            "schema_version must be an integer",
            target,
            instance_path="/schema_version",
        )
    target = ValidationTargetV1(
        artifact_type=schema_id.removeprefix("detailed-analysis."),
        schema_id=schema_id,
        schema_version=max(schema_version, 1),
    )
    if schema_version < 1:
        raise _failure(
            ErrorCategory.SCHEMA,
            ErrorCode.CONSTRAINT_VIOLATION,
            "schema_version must be at least 1",
            target,
            instance_path="/schema_version",
        )
    try:
        model = registry.get(schema_id, schema_version)
    except UnsupportedContractError as exc:
        code = (
            ErrorCode.UNKNOWN_SCHEMA
            if not any(key[0] == schema_id for key, _model in registry.items())
            else ErrorCode.UNSUPPORTED_SCHEMA_VERSION
        )
        raise _failure(ErrorCategory.SCHEMA, code, "contract schema or version is unsupported", target) from exc
    try:
        artifact = model.model_validate(value)
    except ValidationError as exc:
        raise _from_pydantic(exc, target) from None
    for validator in validators:
        try:
            validator(artifact)
        except RuleValidationError as exc:
            issue = exc.issue
            raise _failure(
                issue.category,
                issue.code,
                issue.message,
                target,
                instance_path=issue.instance_path,
                context=issue.context,
            ) from None
    return artifact


def _parse_text(text: str, input_format: InputFormat) -> JsonValue:
    if len(text.encode("utf-8")) > MAX_INPUT_BYTES:
        raise ValueError("input exceeds the safe parsing size")
    if input_format is InputFormat.JSON:
        value = json.loads(text, object_pairs_hook=_unique_json_object, parse_constant=_reject_json_constant)
    else:
        for token in yaml.scan(text):
            if isinstance(token, (AliasToken, AnchorToken, TagToken)):
                raise yaml.YAMLError("explicit YAML tags, anchors, and aliases are not allowed")
        value = yaml.load(text, Loader=_UniqueKeySafeLoader)  # noqa: S506  # type: ignore[no-untyped-call]
    _ensure_json_value(value, depth=0)
    return cast("JsonValue", value)


def _unique_json_object(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for key, value in pairs:
        if key in result:
            raise _InvalidJsonValueError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise _InvalidJsonValueError(f"non-finite JSON number is not allowed: {value}")


def _ensure_json_value(value: object, *, depth: int) -> None:
    if depth > MAX_VALUE_DEPTH:
        raise ValueError("input exceeds the safe parsing depth")
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if math.isfinite(value):
            return
        raise ValueError("non-finite number is not allowed")
    if isinstance(value, list):
        for child in value:
            _ensure_json_value(child, depth=depth + 1)
        return
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValueError("object keys must be strings")
            _ensure_json_value(child, depth=depth + 1)
        return
    raise ValueError("YAML value is not JSON-compatible")


def _from_pydantic(error: ValidationError, target: ValidationTargetV1) -> ContractValidationError:
    detail = error.errors(include_input=False, include_url=False)[0]
    error_type = str(detail["type"])
    if error_type == "missing":
        code = ErrorCode.REQUIRED_FIELD_MISSING
    elif error_type == "extra_forbidden":
        code = ErrorCode.UNKNOWN_FIELD
    elif error_type.endswith("_type") or error_type.endswith("_parsing"):
        code = ErrorCode.TYPE_MISMATCH
    else:
        code = ErrorCode.CONSTRAINT_VIOLATION
    location = detail.get("loc", ())
    instance_path = "".join(f"/{_escape_pointer_token(str(part))}" for part in location)
    return _failure(
        ErrorCategory.SCHEMA,
        code,
        "contract validation failed",
        target,
        instance_path=instance_path,
        context={"pydantic_error_type": error_type},
    )


def _failure(
    category: ErrorCategory,
    code: ErrorCode,
    message: str,
    target: ValidationTargetV1,
    *,
    instance_path: str = "",
    context: dict[str, JsonValue] | None = None,
) -> ContractValidationError:
    artifact = ValidationErrorV1(
        schema_id="detailed-analysis.validation-error",
        schema_version=1,
        error_contract_version=1,
        category=category,
        code=code,
        target=target,
        instance_path=instance_path,
        message=message,
        context=context or {},
    )
    return ContractValidationError(artifact)


def _escape_pointer_token(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")
