"""Explicit specialist delta assessments and lossless prompt deduplication."""

import json
from collections.abc import Mapping
from copy import deepcopy
from typing import Literal, cast

from pydantic import Field, JsonValue, model_validator

from stock_research_llm_orchestrator.contracts.base import StrictContractModel
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.acquisition_analysis import (
    AcquiredEvidence,
    AcquisitionAnalysisV1,
    AcquisitionRequest,
    EvidenceReadRequest,
)
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.web_analysis import WebSpecialistBody


COMPACT_INPUT_INSTRUCTIONS = """
Continuation state and acquired/read results replace duplicated past responses.
Previous Web sources represented as retained_in_dependency are fully available
inside that named specialist/summary/main input, not omitted evidence.
""".strip()

DOWNSTREAM_REASSESSMENT_INSTRUCTIONS = """
Reassess your own previous analysis against the current dependency artifacts and
all changed neutral evidence. Preserve unchanged reading work and investigate
material gaps or contradictions fully. Specialists' incremental assessments are
recorded in incremental_assessments; independently check unchanged decisions
against current originals rather than treating adoption as proof of correctness.
Return a complete final payload or acquire necessary information using your normal
schema. Only specialist schemas support kind="unchanged". Fresh native Web activity
is not mandatory solely because an epoch changed. There is no new research budget
or early stopping rule.
""".strip()

INCREMENTAL_INSTRUCTIONS = """
Assess every new or substantively changed common evidence item against your own
previous analysis, including possible counterevidence that was not previously cited.
An evidence catalog is an index, not financial evidence. Read necessary original
passages using read_evidence or native Web before deciding their significance.
If no material judgment, assumption, scenario, uncertainty or source validity needs
to change, return kind="unchanged", requests=[], payload=null, and explain in state
why every changed item leaves your previous analysis valid. This explicitly adopts
your exact previous payload against the current snapshot; do not rewrite it.
Use unchanged only when own_previous_analysis is supplied and after assessing all
changed evidence. If the impact is uncertain, acquire/read what is needed and then
return a revised complete final payload. A new or contradictory fact can matter
even when none of your previously cited sources changed. Do not interpret source
availability or an unchanged URL as proof of immateriality.
Fresh native Web activity is not mandatory solely because an epoch changed.
Investigate genuine freshness, inconsistency or missing-information gaps fully.
There is no new research budget, search cap or early stopping rule.
""".strip()


class OptimizedSpecialistTurn(StrictContractModel):
    """Allow an auditable unchanged assessment without rewriting an entire analysis."""

    kind: Literal["acquire", "final", "unchanged"]
    requests: tuple[AcquisitionRequest | EvidenceReadRequest, ...] = Field(strict=False)
    state: str = ""
    payload: WebSpecialistBody | None

    @model_validator(mode="after")
    def validate_turn(self) -> OptimizedSpecialistTurn:
        """Reject ambiguous adoption, acquisition, or full-analysis responses."""
        if self.kind == "acquire" and (not self.requests or self.payload is not None):
            raise ValueError("acquisition_turn_requests_required")
        if self.kind == "final" and (self.requests or self.payload is None):
            raise ValueError("acquisition_turn_final_payload_required")
        if self.kind == "unchanged" and (self.requests or self.payload is not None or not self.state.strip()):
            raise ValueError("incremental_unchanged_rationale_required")
        return self


def changed_evidence_ids(
    previous: Mapping[str, AcquiredEvidence], current: Mapping[str, AcquiredEvidence]
) -> list[str]:
    """Compare substantive source states, including changes to existing identities."""

    def state(item: AcquiredEvidence | None) -> object:
        if item is None:
            return None
        return {
            **item.model_dump(mode="json", exclude={"raw_reference", "retrieved_at"}),
            "raw_sha256": item.raw_reference.sha256,
        }

    return sorted(
        identity
        for identity in previous.keys() | current.keys()
        if state(previous.get(identity)) != state(current.get(identity))
    )


def resolve_unchanged(turn_data: dict[str, JsonValue], previous: AcquisitionAnalysisV1 | None) -> dict[str, JsonValue]:
    """Adopt only a real specialist's exact previous payload, never model-supplied replacements."""
    OptimizedSpecialistTurn.model_validate_json(json.dumps(turn_data))
    if turn_data["kind"] != "unchanged" or previous is None or not isinstance(previous.payload, WebSpecialistBody):
        raise ValueError("incremental_previous_specialist_required")
    return cast(dict[str, JsonValue], previous.payload.model_dump(mode="json"))


def compact_request(data: dict[str, JsonValue]) -> dict[str, JsonValue]:
    """Remove redundant copies while preserving all unique facts and source passages."""
    compact = deepcopy(data)
    if "own_previous_turns" in compact:
        compact["own_previous_turns"] = []
    dependencies: dict[str, dict[str, JsonValue]] = {}
    for name in ("specialists", "summaries"):
        values = compact.get(name)
        if isinstance(values, dict):
            dependencies.update({key: value for key, value in values.items() if isinstance(value, dict)})
    main = compact.get("main")
    if isinstance(main, dict):
        dependencies["main"] = main
    sources = compact.get("previous_web_sources")
    if isinstance(sources, dict):
        for name, artifact in dependencies.items():
            payload = artifact.get("payload")
            research = payload.get("research") if isinstance(payload, dict) else None
            existing = research.get("sources") if isinstance(research, dict) else None
            if not isinstance(existing, list):
                continue
            for source in existing:
                if isinstance(source, dict):
                    source_id = source.get("source_id")
                    if isinstance(source_id, str) and source_id in sources and sources[source_id] == source:
                        sources[source_id] = {"retained_in_dependency": name}
    return compact
