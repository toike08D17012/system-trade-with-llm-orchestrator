"""Apply hash-bound replacement responses without rewriting complete analyses."""

import json
import re
from hashlib import sha256
from typing import Literal, cast

from pydantic import Field, JsonValue, model_validator
from pydantic_core import PydanticCustomError

from stock_research_llm_orchestrator.contracts.base import Sha256Hex, StrictContractModel
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.acquisition_analysis import (
    AcquisitionAnalysisV1,
    AcquisitionRequest,
    EvidenceReadRequest,
)
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.web_analysis import (
    WebAggregateBody,
    WebMainBody,
    WebReviewBody,
    WebSpecialistBody,
)
from stock_research_llm_orchestrator.research.input import serialize


DELTA_INSTRUCTIONS = """
Reassess your own previous analysis against changed evidence and dependencies.
Independently assess unchanged decisions against current originals; another
role's adoption is not proof of correctness. Read necessary original passages:
an evidence catalog alone does not substantiate financial claims.
Preserve valid prior work, but update material risks, confidence, assumptions,
source validity and monitoring conditions even when scenario amounts do not change.
Keep state concise: unresolved issues, the next required action and any source
mapping not already retained in the supplied analysis or acquisition/read results.
Do not repeat source passages, acquisition logs or unchanged analysis in state.
If nothing material changes, return kind="unchanged", requests=[], payload=null,
base_artifact_sha256=null, patches=[], and a brief evidence-based reason in state.
For a partial revision, return kind="delta", requests=[], payload=null, a brief
reason in state, and copy own_previous_artifact_sha256 to base_artifact_sha256.
Each patch contains json_pointer into the previous payload, not its artifact
wrapper, and value_json containing the replacement value encoded as valid JSON.
For example, a string value must include its JSON quotation marks inside
value_json. Only replace existing nodes. To add or remove collection items,
replace their containing array/object. Do not replace the root, use nonexistent
paths or provide duplicate or overlapping parent/child paths. Preserve IDs and
update affected references together. The runtime reconstructs and validates the
complete payload; do not return unchanged content merely to reproduce it.
For a broad revision, your normal complete final payload remains available.
Use acquire when necessary; non-delta turns require base_artifact_sha256=null
and patches=[]. Fresh Web search is not required solely by an epoch change.
""".strip()


def _pointer_parts(pointer: str) -> tuple[str, ...]:
    """Decode a non-root JSON pointer while rejecting malformed escapes."""
    if not pointer.startswith("/") or any(re.search(r"~(?![01])", part) for part in pointer.split("/")[1:]):
        raise ValueError("delta_patch_path_invalid")
    return tuple(part.replace("~1", "/").replace("~0", "~") for part in pointer.split("/")[1:])


class PayloadReplacement(StrictContractModel):
    """Replace one existing payload node with a JSON-encoded value."""

    json_pointer: str
    # A string keeps the provider schema closed even for arbitrary object values.
    value_json: str


class _DeltaTurn(StrictContractModel):
    """A role response with an optional exact-base partial revision."""

    kind: Literal["acquire", "final", "unchanged", "delta"]
    requests: tuple[AcquisitionRequest | EvidenceReadRequest, ...] = Field(strict=False)
    state: str = ""
    payload: WebSpecialistBody | WebAggregateBody | WebMainBody | WebReviewBody | None
    base_artifact_sha256: Sha256Hex | None = None
    patches: tuple[PayloadReplacement, ...] = Field(default=(), strict=False)

    @model_validator(mode="after")
    def validate_turn(self) -> _DeltaTurn:
        """Keep acquisition, full output, adoption and replacements unambiguous."""
        if self.kind == "acquire" and (not self.requests or self.payload is not None):
            raise ValueError("acquisition_turn_requests_required")
        if self.kind == "final" and (self.requests or self.payload is None):
            raise ValueError("acquisition_turn_final_payload_required")
        if self.kind in {"unchanged", "delta"} and (
            self.requests or self.payload is not None or not self.state.strip()
        ):
            raise ValueError("delta_reassessment_rationale_required")
        if self.kind != "delta":
            if self.base_artifact_sha256 is not None or self.patches:
                raise ValueError("delta_unexpected_replacements")
            return self
        if self.base_artifact_sha256 is None or not self.patches:
            raise ValueError("delta_replacements_required")
        paths: dict[tuple[str, ...], str] = {}
        for patch in self.patches:
            parts = _pointer_parts(patch.json_pointer)
            for other, previous_pointer in paths.items():
                if parts[: len(other)] == other or other[: len(parts)] == parts:
                    raise PydanticCustomError(
                        "delta_patch_paths_overlap",
                        "delta_patch_paths_overlap: {first} overlaps {second}",
                        {"first": previous_pointer, "second": patch.json_pointer},
                    )
            paths[parts] = patch.json_pointer
        return self


class DeltaSpecialistTurn(_DeltaTurn):
    """One specialist's typed incremental response."""

    payload: WebSpecialistBody | None


class DeltaAggregateTurn(_DeltaTurn):
    """One organizer's typed incremental response."""

    payload: WebAggregateBody | None


class DeltaMainTurn(_DeltaTurn):
    """The synthesis agent's typed incremental response."""

    payload: WebMainBody | None


class DeltaReviewTurn(_DeltaTurn):
    """The primary reviewer's typed incremental response."""

    payload: WebReviewBody | None


def _turn_model(previous: AcquisitionAnalysisV1 | None) -> type[_DeltaTurn]:
    """Select the response schema from the trusted previous role."""
    if previous is None:
        raise ValueError("delta_previous_analysis_required")
    if previous.role.endswith(("capital", "income")):
        return DeltaSpecialistTurn
    if previous.role.endswith("aggregator"):
        return DeltaAggregateTurn
    return DeltaMainTurn if previous.role == "orchestrator" else DeltaReviewTurn


def _child(node: JsonValue, token: str) -> JsonValue:
    """Read an existing node without accepting ambiguous list indexes."""
    if isinstance(node, dict) and token in node:
        return node[token]
    if isinstance(node, list) and re.fullmatch(r"0|[1-9][0-9]*", token) and int(token) < len(node):
        return node[int(token)]
    raise ValueError("delta_patch_path_invalid")


def _reject_constant(value: str) -> JsonValue:
    """Reject non-JSON numeric constants accepted by the standard decoder."""
    del value
    raise ValueError("delta_patch_value_invalid")


def resolve_delta(turn_data: dict[str, JsonValue], previous: AcquisitionAnalysisV1 | None) -> dict[str, JsonValue]:
    """Apply replacements to an exact prior artifact and validate its payload."""
    parsed = _turn_model(previous).model_validate_json(json.dumps(turn_data))
    if parsed.kind != "delta":
        raise ValueError("delta_response_required")
    assert previous is not None
    if parsed.base_artifact_sha256 != sha256(serialize(previous)).hexdigest():
        raise ValueError("delta_base_artifact_mismatch")
    payload = cast(dict[str, JsonValue], previous.payload.model_dump(mode="json"))
    for patch in parsed.patches:
        parts = _pointer_parts(patch.json_pointer)
        node: JsonValue = payload
        for token in parts[:-1]:
            node = _child(node, token)
        _child(node, parts[-1])
        try:
            value = cast(JsonValue, json.loads(patch.value_json, parse_constant=_reject_constant))
        except ValueError, TypeError:
            raise ValueError("delta_patch_value_invalid") from None
        if isinstance(node, dict):
            node[parts[-1]] = value
        else:
            assert isinstance(node, list)
            node[int(parts[-1])] = value
    validated = type(previous.payload).model_validate_json(json.dumps(payload))
    return cast(dict[str, JsonValue], validated.model_dump(mode="json"))


def resolve_adoption(turn_data: dict[str, JsonValue], previous: AcquisitionAnalysisV1 | None) -> dict[str, JsonValue]:
    """Adopt the exact prior payload for any reassessing research role."""
    parsed = _turn_model(previous).model_validate_json(json.dumps(turn_data))
    if parsed.kind != "unchanged":
        raise ValueError("delta_adoption_required")
    assert previous is not None
    return cast(dict[str, JsonValue], previous.payload.model_dump(mode="json"))
