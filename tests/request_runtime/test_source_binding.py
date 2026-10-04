"""Fail closed on unsupported or inconsistent source snapshots."""

from dataclasses import replace
from datetime import date

import pytest

from stock_research_llm_orchestrator.configuration import ConfigurationArtifact
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.policies import SourceProfileV1
from stock_research_llm_orchestrator.requests import AttemptResult, SourceBinding, bind_source


def binding(artifacts: tuple[ConfigurationArtifact, ...]) -> SourceBinding:
    """Select the approved synthetic source."""
    result = bind_source(artifacts, "fixture", 1, 1, date(2026, 9, 20))
    assert isinstance(result, SourceBinding)
    return result


@pytest.mark.parametrize("evaluation", [date(2000, 1, 1), date(2100, 1, 1)])
def test_approval_date(artifacts: tuple[ConfigurationArtifact, ...], evaluation: date) -> None:
    """Reject use outside the approved interval."""
    assert isinstance(bind_source(artifacts, "fixture", 1, 1, evaluation), AttemptResult)


def test_ambiguous_and_missing(artifacts: tuple[ConfigurationArtifact, ...]) -> None:
    """Require exactly one profile and approval."""
    for candidates in ((), artifacts * 2, artifacts[:1], artifacts[1:]):
        assert isinstance(bind_source(candidates, "fixture", 1, 1, date(2026, 9, 20)), AttemptResult)


@pytest.mark.parametrize(
    "field,value", [("max_concurrency", 2), ("burst", 2), ("enabled", False), ("credential_scope_alias", "different")]
)
def test_unsupported_profile(artifacts: tuple[ConfigurationArtifact, ...], field: str, value: object) -> None:
    """Reject unsupported profile settings before any transport exists."""
    changed = tuple(
        replace(item, model=item.model.model_copy(update={field: value}))
        if isinstance(item.model, SourceProfileV1)
        else item
        for item in artifacts
    )
    assert isinstance(bind_source(changed, "fixture", 1, 1, date(2026, 9, 20)), AttemptResult)


@pytest.mark.parametrize(
    "field,update",
    [
        ("min_interval_seconds", {"unit": "minute"}),
        ("min_interval_seconds", {"value": float("nan")}),
        ("rate_window_seconds", {"value": 0}),
        ("rate_window_seconds", {"value": float("inf")}),
        ("rate_requests", {"value": 1.5}),
        ("rate_requests", {"value": 0}),
        ("rate_requests", {"knowledge_status": "unknown", "value": None}),
        ("cache_policy", {"enabled": True}),
        ("batch_policy", {"support_status": "known"}),
        (
            "cooldown_policy",
            {
                "default_cooldown_seconds": {
                    "knowledge_status": "known",
                    "value": 30,
                    "unit": "second",
                    "evidence_reference_ids": (),
                }
            },
        ),
    ],
)
def test_profile_subset(
    artifacts: tuple[ConfigurationArtifact, ...],
    field: str,
    update: dict[str, object],
) -> None:
    """Refuse unknown units, invalid rates, and unsupported enabled features."""
    changed = []
    for item in artifacts:
        if isinstance(item.model, SourceProfileV1):
            setting = getattr(item.model, field).model_copy(update=update)
            item = replace(item, model=item.model.model_copy(update={field: setting}))
        changed.append(item)
    assert isinstance(bind_source(tuple(changed), "fixture", 1, 1, date(2026, 9, 20)), AttemptResult)


def test_exact_dates_hash_and_unsealed_binding(artifacts: tuple[ConfigurationArtifact, ...]) -> None:
    """Check inclusive start, exclusive recheck, exact hashes, and factory use."""
    source = binding(artifacts)
    assert source.approval.effective_on is not None
    assert source.approval.recheck_due_on is not None
    assert isinstance(
        bind_source(artifacts, "fixture", 1, 1, date.fromisoformat(source.approval.effective_on)), SourceBinding
    )
    assert isinstance(
        bind_source(artifacts, "fixture", 1, 1, date.fromisoformat(source.approval.recheck_due_on)), AttemptResult
    )
    changed = tuple(replace(item, sha256="0" * 64) for item in artifacts)
    assert isinstance(bind_source(changed, "fixture", 1, 1, date(2026, 9, 20)), AttemptResult)
    unsealed = SourceBinding(
        source.approval, source.profile, source.approval_reference, source.profile_reference, source.evaluation_date
    )
    with pytest.raises(ValueError, match="unsupported_source_binding"):
        unsealed.limits()
