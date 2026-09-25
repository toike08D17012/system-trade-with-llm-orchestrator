"""Tests for policy, market, and source configuration contracts."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.policies import (
    DetailedAnalysisPolicyV1,
    MarketProfileV1,
    ReviewAuditPolicyV1,
    SessionContinuationPolicyV1,
    SourceApprovalV1,
    SourceProfileV1,
    WebResearchPolicyV1,
)
from stock_research_llm_orchestrator.contracts.validation.wrapper import InputFormat, validate_text


FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "contracts" / "detailed-analysis" / "v1"
CONFIG_ROOT = Path(__file__).parents[2] / "config"


def _fixture(artifact_name: str, case_name: str) -> str:
    return (FIXTURE_ROOT / artifact_name / "valid" / case_name).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("artifact_name", "case_name", "model"),
    [
        ("detailed-analysis-policy", "initial-mvp.json", DetailedAnalysisPolicyV1),
        ("web-research-policy", "independent-search.json", WebResearchPolicyV1),
        ("market-profile", "xtks-calendar-disabled.json", MarketProfileV1),
        ("review-audit-policy", "approved-v1.json", ReviewAuditPolicyV1),
        ("session-continuation-policy", "approved-v1.json", SessionContinuationPolicyV1),
        ("source-approval", "unverified-disabled.json", SourceApprovalV1),
        ("source-profile", "unverified-disabled.json", SourceProfileV1),
    ],
)
def test_valid_policy_and_profile_fixture(artifact_name: str, case_name: str, model: type[object]) -> None:
    """Dispatch each public policy and profile through the common validator."""
    artifact = validate_text(_fixture(artifact_name, case_name), InputFormat.JSON)

    assert isinstance(artifact, model)


def test_detailed_analysis_policy_rejects_changed_horizon_boundary() -> None:
    """Keep the approved 12-24 and 24-60 month ranges exact."""
    payload = json.loads(_fixture("detailed-analysis-policy", "initial-mvp.json"))
    payload["horizons"][0]["minimum_months"] = 6

    with pytest.raises(ValidationError, match="approved medium- and long-term"):
        DetailedAnalysisPolicyV1.model_validate(payload)


def test_unretrieved_market_calendar_cannot_drive_runtime_dates() -> None:
    """Keep current-date resolution disabled until calendar data is verified."""
    payload = json.loads(_fixture("market-profile", "xtks-calendar-disabled.json"))
    payload["enabled_for_runtime_date_resolution"] = True

    with pytest.raises(ValidationError, match="requires known calendar data"):
        MarketProfileV1.model_validate(payload)


def test_draft_source_cannot_enable_online_use() -> None:
    """Require an approved and complete source record before network use."""
    payload = json.loads(_fixture("source-approval", "unverified-disabled.json"))
    payload["online_use_allowed"] = True

    with pytest.raises(ValidationError, match="online use requires approved"):
        SourceApprovalV1.model_validate(payload)


def test_unknown_source_profile_uses_conservative_disabled_limits() -> None:
    """Require max concurrency and burst of one while interval is unknown."""
    payload = json.loads(_fixture("source-profile", "unverified-disabled.json"))
    payload["max_concurrency"] = 2

    with pytest.raises(ValidationError, match="max_concurrency 1 and burst 1"):
        SourceProfileV1.model_validate(payload)


def test_source_profile_rejects_secret_field() -> None:
    """Keep credential values out of profiles and logs."""
    payload = json.loads(_fixture("source-profile", "unverified-disabled.json"))
    payload["api_key"] = "secret-value"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        SourceProfileV1.model_validate(payload)


@pytest.mark.parametrize(
    "relative_path",
    [
        "policies/detailed-analysis/v1.yaml",
        "policies/web-research/v1.yaml",
        "policies/review-audit/v1.yaml",
        "policies/session-continuation/v1.yaml",
        "market-profiles/xtks/v1.yaml",
        "source-approvals/boj/v1.yaml",
        "source-profiles/boj/v1.yaml",
        "source-approvals/jpx/v1.yaml",
        "source-profiles/jpx/v1.yaml",
        "source-approvals/yfinance/v1.yaml",
        "source-approvals/yfinance/v2.yaml",
        "source-profiles/yfinance/v1.yaml",
        "source-profiles/yfinance/v2.yaml",
        "source-profiles/yfinance/v3.yaml",
    ],
)
def test_versioned_yaml_configuration_is_contract_valid(relative_path: str) -> None:
    """Validate committed configuration through the same safe YAML boundary."""
    path = CONFIG_ROOT / relative_path

    validate_text(path.read_text(encoding="utf-8"), InputFormat.YAML)


def test_yfinance_profile_uses_the_approved_conservative_limits() -> None:
    """Bind enabled yfinance use to the exact approval and conservative rate policy."""
    import hashlib

    approval_path = CONFIG_ROOT / "source-approvals/yfinance/v1.yaml"
    profile_path = CONFIG_ROOT / "source-profiles/yfinance/v1.yaml"
    approval = validate_text(approval_path.read_text(encoding="utf-8"), InputFormat.YAML)
    profile = validate_text(profile_path.read_text(encoding="utf-8"), InputFormat.YAML)

    assert isinstance(approval, SourceApprovalV1)
    assert isinstance(profile, SourceProfileV1)
    assert approval.status.value == "approved"
    assert approval.online_use_allowed
    assert approval.external_agent_transfer_allowed
    assert approval.effective_on == "2026-09-14"
    assert approval.recheck_due_on == "2026-12-14"
    assert profile.source_approval_reference.sha256 == hashlib.sha256(approval_path.read_bytes()).hexdigest()
    assert profile.source_approval_status.value == "approved"
    assert profile.enabled
    assert profile.max_concurrency == 1
    assert profile.min_interval_seconds.value == 2.5
    assert profile.rate_requests.value == 2
    assert profile.rate_window_seconds.value == 5
    assert profile.burst == 1
    assert not profile.cooldown_policy.automatic_retry_allowed
    assert not profile.direct_connection_fallback_allowed


def test_yfinance_profile_v2_preserves_failed_compatibility_gate() -> None:
    """Fail closed when the latest stable yfinance cannot disable its status retry."""
    import hashlib

    approval_path = CONFIG_ROOT / "source-approvals/yfinance/v1.yaml"
    profile_path = CONFIG_ROOT / "source-profiles/yfinance/v2.yaml"
    approval = validate_text(approval_path.read_text(encoding="utf-8"), InputFormat.YAML)
    profile = validate_text(profile_path.read_text(encoding="utf-8"), InputFormat.YAML)

    assert isinstance(approval, SourceApprovalV1)
    assert isinstance(profile, SourceProfileV1)
    assert profile.source_approval_reference.sha256 == hashlib.sha256(approval_path.read_bytes()).hexdigest()
    assert not profile.enabled
    assert not profile.cooldown_policy.automatic_retry_allowed
    assert not profile.direct_connection_fallback_allowed


def test_latest_yfinance_profile_enables_approved_status_fallback() -> None:
    """Enable v1.7.0 only under the revised approval and controlled-session boundary."""
    import hashlib

    approval_path = CONFIG_ROOT / "source-approvals/yfinance/v2.yaml"
    profile_path = CONFIG_ROOT / "source-profiles/yfinance/v3.yaml"
    approval = validate_text(approval_path.read_text(encoding="utf-8"), InputFormat.YAML)
    profile = validate_text(profile_path.read_text(encoding="utf-8"), InputFormat.YAML)

    assert isinstance(approval, SourceApprovalV1)
    assert isinstance(profile, SourceProfileV1)
    assert approval.approval_version == 2
    assert profile.profile_version == 3
    assert profile.policy_version == 3
    assert profile.source_approval_reference.sha256 == hashlib.sha256(approval_path.read_bytes()).hexdigest()
    assert profile.enabled
    assert not profile.cooldown_policy.automatic_retry_allowed
    assert not profile.direct_connection_fallback_allowed
