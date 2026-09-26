"""Review-bound Yahoo production composition without network or storage."""

from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from stock_research_llm_orchestrator.configuration import validate_configuration
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.external_requests import GateScope
from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.research import AgentRole
from stock_research_llm_orchestrator.requests.models import SourceBinding, bind_source
from stock_research_llm_orchestrator.sources.yfinance.coordinated_session import YahooRequestIdentity
from stock_research_llm_orchestrator.sources.yfinance.models import YfinanceDailyIntent
from stock_research_llm_orchestrator.sources.yfinance.production_policy import (
    YahooPolicyError,
    YahooProductionPolicy,
    compose_yahoo_production_policy,
)


CHART = YahooRequestIdentity("GET", "query2.finance.yahoo.com", "chart", "chart")


def reviewed_binding() -> SourceBinding:
    """Read reviewed artifacts through the same validator as application configuration."""
    binding = bind_source(validate_configuration(Path("config")), "yfinance", 3, 3, date(2026, 9, 26))
    assert isinstance(binding, SourceBinding)
    return binding


def test_exact_reviewed_gate_composition() -> None:
    """All scopes preserve the approved local ceiling and rate domain."""
    policy = compose_yahoo_production_policy(reviewed_binding())
    gates = policy.gate_policy()
    assert set(gates.limits) == set(GateScope)
    for scope, limit in gates.limits.items():
        expected = (
            (0, 60, 60)
            if scope in {GateScope.GLOBAL, GateScope.EGRESS, GateScope.TASK, GateScope.ROLE}
            else (2.5, 2, 5)
        )
        assert limit.max_concurrency == 1
        assert (limit.min_interval_seconds, limit.requests_per_window, limit.window_seconds) == expected
    keys = policy.gate_keys(CHART, task_id="task-one", role=AgentRole.CODEX_WORKER)
    assert keys.provider == policy.rate_domain == "yahoo-finance"
    assert keys.egress == "default-egress"
    assert keys.credential == "anonymous:yahoo-finance"
    assert keys.operation == "chart"
    assert keys.role == "codex_worker"
    gates.limits.clear()
    assert len(policy.gate_policy().limits) == 8


@pytest.mark.parametrize(
    "changes",
    [
        {"profile_version": 4},
        {"policy_version": 2},
        {"rate_domain": "yfinance"},
        {"egress_scope": "other"},
        {"enabled": False},
        {"credential_scope_alias": "secret"},
        {"direct_connection_fallback_allowed": True},
    ],
)
def test_mutated_profile_rejected(changes: dict[str, object]) -> None:
    """A factory token alone never authorizes a different profile policy."""
    source = reviewed_binding()
    with pytest.raises(YahooPolicyError, match="unsupported_yahoo_production_binding"):
        compose_yahoo_production_policy(replace(source, profile=source.profile.model_copy(update=changes)))


def test_unsealed_expired_and_weakened_rate_rejected() -> None:
    """Reject unsealed bindings, expired approvals and changed numeric rate settings."""
    source = reviewed_binding()
    with pytest.raises(YahooPolicyError):
        compose_yahoo_production_policy(replace(source, _factory_token=None))
    with pytest.raises(YahooPolicyError):
        YahooProductionPolicy(source).gate_policy()
    with pytest.raises(YahooPolicyError):
        compose_yahoo_production_policy(source).validate(date(2026, 12, 25))
    changed = source.profile.model_copy(
        update={"min_interval_seconds": source.profile.min_interval_seconds.model_copy(update={"value": 1.0})}
    )
    with pytest.raises(YahooPolicyError):
        compose_yahoo_production_policy(replace(source, profile=changed))
    with pytest.raises(YahooPolicyError):
        compose_yahoo_production_policy(
            replace(source, profile_reference=source.profile_reference.model_copy(update={"sha256": "0" * 64}))
        )


def test_logical_request_and_cache_derive_reviewed_lineage() -> None:
    """Admission callers supply runtime identity, never independent approval versions."""
    policy = compose_yahoo_production_policy(reviewed_binding())
    request = policy.logical_request(
        logical_request_id="logical-one",
        task_id="task-one",
        request_fingerprint="a" * 64,
        created_at=datetime(2026, 9, 26, tzinfo=UTC),
    )
    assert request.source_approval_version == 2
    assert request.source_profile_version == 3
    assert request.source_id == "yfinance"
    assert request.credential_scope_alias is None
    assert request.egress_scope == "default-egress"
    assert policy.cache_policy().enabled is False


def test_chart_policy_is_json_utf8_without_redirects() -> None:
    """Only reviewed chart response constraints are returned."""
    policy = compose_yahoo_production_policy(reviewed_binding()).transport_policy_for(CHART)
    assert policy.max_response_bytes is None
    assert policy.allowed_media_types == ("application/json",)
    assert policy.allowed_encodings == ("utf-8",)
    assert policy.allowed_redirect_origins == ()


@pytest.mark.parametrize(
    "identity",
    [
        YahooRequestIdentity("GET", "fc.yahoo.com", "cookie", "cookie_basic"),
        YahooRequestIdentity("GET", "query1.finance.yahoo.com", "crumb", "crumb_basic"),
        YahooRequestIdentity("GET", "query2.finance.yahoo.com", "crumb", "crumb_csrf"),
        YahooRequestIdentity("GET", "guce.yahoo.com", "consent", "consent_form"),
        YahooRequestIdentity("POST", "consent.yahoo.com", "consent", "consent_collect"),
        YahooRequestIdentity("GET", "guce.yahoo.com", "consent", "consent_copy"),
    ],
)
def test_approved_auxiliary_formats(identity: YahooRequestIdentity) -> None:
    """Endpoint approval is not an implicit response size or media approval."""
    policy = compose_yahoo_production_policy(reviewed_binding())
    result = policy.transport_policy_for(identity)
    assert result.allowed_encodings == ("utf-8",)
    assert result.allowed_media_types == (
        ("text/plain",) if identity.resource_class == "crumb" else ("text/html", "text/plain")
    )


def test_identity_and_task_validation_is_sanitized() -> None:
    """Arbitrary paths and URL-bearing task values cannot enter gate aliases."""
    policy = compose_yahoo_production_policy(reviewed_binding())
    with pytest.raises(YahooPolicyError, match="unapproved_yahoo_request_identity"):
        policy.transport_policy_for(YahooRequestIdentity("GET", "fc.yahoo.com", "chart", "chart"))
    with pytest.raises(YahooPolicyError, match="invalid_yahoo_runtime_identity"):
        policy.gate_keys(CHART, task_id="https://private-canary/", role=AgentRole.CODEX_WORKER)


def test_intent_fingerprint_covers_the_complete_binding() -> None:
    """Every meaningful intent, task and agent-role change has a distinct stable hash."""
    policy = compose_yahoo_production_policy(reviewed_binding())
    intent = YfinanceDailyIntent(
        symbol="7203.T",
        jpx_code="7203",
        jpx_snapshot_on="2026-08-31",
        mic="XTKS",
        market_segment="Prime",
        start="2026-09-01",
        end="2026-09-03",
    )
    original = policy.intent_fingerprint(intent, task_id="task-one", role=AgentRole.CODEX_WORKER)
    assert original == policy.intent_fingerprint(
        YfinanceDailyIntent.model_validate(dict(reversed(list(intent.model_dump().items())))),
        task_id="task-one",
        role=AgentRole.CODEX_WORKER,
    )
    for change in (
        {"symbol": "1301.T", "jpx_code": "1301"},
        {"start": "2026-08-01"},
        {"end": "2026-09-04"},
        {"jpx_snapshot_on": "2026-08-30"},
        {"market_segment": "Growth"},
    ):
        changed = YfinanceDailyIntent.model_validate({**intent.model_dump(), **change})
        assert original != policy.intent_fingerprint(changed, task_id="task-one", role=AgentRole.CODEX_WORKER)
    assert original != policy.intent_fingerprint(intent, task_id="task-two", role=AgentRole.CODEX_WORKER)
    assert original != policy.intent_fingerprint(intent, task_id="task-one", role=AgentRole.CLAUDE_WORKER)
    with pytest.raises(YahooPolicyError, match="invalid_yahoo_intent_identity"):
        policy.intent_fingerprint(
            intent.model_copy(update={"mic": "XNAS"}), task_id="task-one", role=AgentRole.CODEX_WORKER
        )
