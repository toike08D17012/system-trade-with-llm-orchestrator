"""Offline BOJ single-exchange acceptance and durable failure tests."""

import json
import shutil
import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from stock_research_llm_orchestrator.preparation.fx_evidence import (
    FxIndex,
    acquire_fx_evidence,
    read_bundle,
    validate_fx_evidence,
)
from stock_research_llm_orchestrator.preparation.fx_evidence_cli import main
from stock_research_llm_orchestrator.sources.boj.production_policy import load_boj_binding


NOW = datetime(2026, 9, 26, tzinfo=UTC)
FIXTURE = Path("tests/fixtures/sources/boj/fxerd04.json")


def acquire(
    root: Path,
    body: bytes | None = None,
    *,
    status: int = 200,
    retry_after: str | None = None,
    now: datetime = NOW,
    destination: str = "fx",
) -> FxIndex:
    """Exercise real runtime storage with only the physical network replaced."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        headers = {"Content-Type": "application/json"}
        if retry_after is not None:
            headers["Retry-After"] = retry_after
        return httpx.Response(status, headers=headers, content=body if body is not None else FIXTURE.read_bytes())

    result = acquire_fx_evidence(
        config=Path("config"),
        runtime=root / ".runtime",
        runs=root / "runs",
        destination=destination,
        task_id="task-fx",
        start=date(2026, 9, 21),
        end=date(2026, 9, 23),
        clock=lambda: now,
        transport=httpx.MockTransport(handler),
    )
    assert len(calls) == 1
    return result


def _results(root: Path) -> list[tuple[str]]:
    database = next((root / ".runtime").glob("*.sqlite3"))
    with sqlite3.connect(database) as connection:
        return connection.execute("SELECT outcome FROM logical_results").fetchall()


def test_acquire_retains_exact_raw_and_success_after_publication(tmp_path: Path) -> None:
    """Reconstruct the accepted evidence and detect normalized-value tampering."""
    result = acquire(tmp_path)
    assert result.status == "accepted_with_limitations"
    assert result.null_dates == ("2026-09-22",)
    files = read_bundle(tmp_path / "runs/fx")
    assert files["body.bin"] == FIXTURE.read_bytes()
    assert json.loads(files["metadata.json"])["received_at"] == NOW.isoformat()
    validate_fx_evidence(files)
    assert _results(tmp_path) == [("succeeded",)]
    files["normalized.json"] = files["normalized.json"].replace(b"147.25", b"147.26")
    with pytest.raises(ValueError):
        validate_fx_evidence(files)


@pytest.mark.parametrize("fault", ["unit", "name", "period", "outside", "order", "duplicate", "pagination"])
def test_invalid_source_never_becomes_logical_success(tmp_path: Path, fault: str) -> None:
    """Retain diagnostics while preventing invalid source metadata from reaching consumers."""
    body = json.loads(FIXTURE.read_bytes())
    series = body["RESULTSET"][0]
    if fault == "unit":
        series["UNIT"] = "USD per JPY"
    elif fault == "name":
        series["NAME_OF_TIME_SERIES"] = "09:00 rate"
    elif fault == "period":
        body["PARAMETER"]["STARTDATE"] = "202608"
    elif fault == "outside":
        series["VALUES"]["SURVEY_DATES"][0] = 20260831
    elif fault == "order":
        series["VALUES"]["SURVEY_DATES"].reverse()
    elif fault == "duplicate":
        series["VALUES"]["SURVEY_DATES"][1] = 20260921
    else:
        body["NEXTPOSITION"] = 1
    with pytest.raises(ValueError, match="boj_source_validation_failed"):
        acquire(tmp_path, json.dumps(body).encode())
    assert _results(tmp_path) == [("failed",)]
    assert not (tmp_path / "runs/fx/index.json").exists()
    assert (tmp_path / "runs/fx/unaccepted-body.bin").exists()


@pytest.mark.parametrize("value", [0, -1])
def test_nonpositive_values_are_retained_as_quality_failures(tmp_path: Path, value: int) -> None:
    """Preserve finite source values without treating zero or negative FX as usable."""
    body = json.loads(FIXTURE.read_bytes())
    body["RESULTSET"][0]["VALUES"]["VALUES"][0] = value
    result = acquire(tmp_path, json.dumps(body).encode())
    assert result.invalid_dates == ("2026-09-21",)
    assert result.status == "accepted_with_limitations"


def test_rate_limit_persists_across_invocations(tmp_path: Path) -> None:
    """Keep provider cooldown effective after releasing and reacquiring runtime ownership."""
    with pytest.raises(ValueError, match="rate_limited"):
        acquire(tmp_path, status=429, retry_after="120")
    with pytest.raises(RuntimeError):
        acquire(tmp_path, now=NOW + timedelta(seconds=61), destination="second")
    assert _results(tmp_path) == [("failed",), ("failed",)]


@pytest.mark.parametrize("retry_after", [None, "invalid", "-1"])
def test_invalid_retry_after_stops_without_resending(tmp_path: Path, retry_after: str | None) -> None:
    """Reject rate-limited responses without inventing a default cooldown."""
    with pytest.raises(ValueError, match="provider_error"):
        acquire(tmp_path, status=429, retry_after=retry_after)
    assert _results(tmp_path) == [("failed",)]


def test_disabled_changed_or_expired_policy_is_rejected(tmp_path: Path) -> None:
    """Reject changed approved bytes and expired authorization before sending."""
    config = tmp_path / "config"
    shutil.copytree("config", config)
    path = config / "source-profiles/boj/v2.yaml"
    path.write_text(path.read_text().replace("value: 60", "value: 1"))
    with pytest.raises(ValueError):
        load_boj_binding(config, NOW.date())
    with pytest.raises(ValueError):
        load_boj_binding(Path("config"), date(2026, 12, 26))


def test_cli_requires_network_flag() -> None:
    """Require explicit network intent at the command boundary."""
    with pytest.raises(SystemExit):
        main(["acquire"])


@pytest.mark.parametrize("failure,expected", [("timeout", "unknown"), ("redirect", "failed"), ("media", "failed")])
def test_transport_failure_outcomes(tmp_path: Path, failure: str, expected: str) -> None:
    """Distinguish unknown exchanges from received invalid responses."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("sensitive provider details")
        if failure == "redirect":
            return httpx.Response(302, headers={"Location": "https://example.invalid"})
        return httpx.Response(200, headers={"Content-Type": "text/html"}, content=b"invalid")

    with pytest.raises(ValueError):
        acquire_fx_evidence(
            config=Path("config"),
            runtime=tmp_path / ".runtime",
            runs=tmp_path / "runs",
            destination="fx",
            task_id="task-fx",
            start=date(2026, 9, 21),
            end=date(2026, 9, 23),
            clock=lambda: NOW,
            transport=httpx.MockTransport(handler),
        )
    assert len(calls) == 1
    assert _results(tmp_path) == [(expected,)]


def test_publication_failure_does_not_finalize_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Leave publication interruption for fenced recovery rather than claiming success."""
    from stock_research_llm_orchestrator.requests.raw_artifacts import RawArtifactPublisher

    def fail(*args: object, **kwargs: object) -> None:
        raise OSError("synthetic publication failure")

    monkeypatch.setattr(RawArtifactPublisher, "publish", fail)
    with pytest.raises(OSError):
        acquire(tmp_path)
    assert _results(tmp_path) == []
    assert not (tmp_path / "runs/fx/index.json").exists()


def test_expired_lease_cannot_publish_or_claim_success(tmp_path: Path) -> None:
    """Prevent an expired owner from publishing a received response."""
    now = NOW

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal now
        now += timedelta(seconds=121)
        return httpx.Response(200, headers={"Content-Type": "application/json"}, content=FIXTURE.read_bytes())

    with pytest.raises(RuntimeError, match="lease"):
        acquire_fx_evidence(
            config=Path("config"),
            runtime=tmp_path / ".runtime",
            runs=tmp_path / "runs",
            destination="fx",
            task_id="task-fx",
            start=date(2026, 9, 21),
            end=date(2026, 9, 23),
            clock=lambda: now,
            transport=httpx.MockTransport(handler),
        )
    assert _results(tmp_path) == []
    assert not (tmp_path / "runs/fx/index.json").exists()


def test_invalid_private_output_is_rejected_before_network(tmp_path: Path) -> None:
    """Validate raw publication directory permissions before consuming a provider send."""
    (tmp_path / "runs").mkdir(mode=0o755)
    with pytest.raises(ValueError, match="private_permissions"):
        acquire(tmp_path)
    assert not (tmp_path / "runs/fx").exists()


def test_failed_source_result_propagates_to_active_follower(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A follower attached before receipt receives the source validation failure."""
    from stock_research_llm_orchestrator.requests.production import (
        AdmissionDecision,
        ProductionCachePolicy,
        ProductionLogicalRequest,
        QueuePolicy,
        RuntimeLease,
    )
    from stock_research_llm_orchestrator.requests.storage import ProductionRequestRepository

    original = ProductionRequestRepository.admit_logical_request

    def admit(
        self: ProductionRequestRepository,
        request: ProductionLogicalRequest,
        rate_domain: str,
        cache_policy: ProductionCachePolicy,
        lease: RuntimeLease,
        now: datetime,
        queue_policy: QueuePolicy,
    ) -> AdmissionDecision:
        result = original(self, request, rate_domain, cache_policy, lease, now, queue_policy)
        follower = request.model_copy(update={"logical_request_id": "follower-fx"})
        original(self, follower, rate_domain, cache_policy, lease, now, queue_policy)
        return result

    monkeypatch.setattr(ProductionRequestRepository, "admit_logical_request", admit)
    body = json.loads(FIXTURE.read_bytes())
    body["RESULTSET"][0]["UNIT"] = "wrong"
    with pytest.raises(ValueError, match="boj_source_validation_failed"):
        acquire(tmp_path, json.dumps(body).encode())
    assert _results(tmp_path) == [("failed",), ("failed",)]


def test_diagnostic_revalidation_keeps_failure_and_uses_no_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Republish validated exact raw while preserving the original failed acquisition record."""
    import socket

    from stock_research_llm_orchestrator.preparation.fx_evidence import revalidate_fx_diagnostic
    from stock_research_llm_orchestrator.sources.boj.code_api import BojFxCodeAdapter

    def reject_source(*args: object, **kwargs: object) -> None:
        raise ValueError("synthetic old validator defect")

    with monkeypatch.context() as previous:
        previous.setattr(BojFxCodeAdapter, "parse_for_intent", reject_source)
        with pytest.raises(ValueError):
            acquire(tmp_path)
    original = read_bundle(tmp_path / "runs/fx")
    database = next((tmp_path / ".runtime").glob("*.sqlite3"))
    with sqlite3.connect(database) as connection:
        logical_id = connection.execute("SELECT logical_request_id FROM logical_results").fetchone()[0]

    def reject_network(*args: object, **kwargs: object) -> None:
        pytest.fail("diagnostic revalidation attempted network I/O")

    monkeypatch.setattr(socket.socket, "connect", reject_network)
    monkeypatch.setattr(socket, "getaddrinfo", reject_network)
    result = revalidate_fx_diagnostic(
        config=Path("config"),
        runtime=tmp_path / ".runtime",
        runs=tmp_path / "runs",
        diagnostic=tmp_path / "runs/fx",
        destination="revalidated",
        task_id="task-fx",
        logical_request_id=logical_id,
        start=date(2026, 9, 21),
        end=date(2026, 9, 23),
        clock=lambda: NOW,
    )
    assert result.status == "accepted_with_limitations"
    assert _results(tmp_path) == [("failed",)]
    assert read_bundle(tmp_path / "runs/fx") == original
    files = read_bundle(tmp_path / "runs/revalidated")
    assert files["body.bin"] == original["unaccepted-body.bin"]
    validate_fx_evidence(files)
    assert json.loads(files["acquisition-result.json"])["outcome"] == "failed"
    files["acquisition-result.json"] = files["acquisition-result.json"].replace(b'"failed"', b'"unknown"')
    with pytest.raises(ValueError):
        validate_fx_evidence(files)


def test_category_label_is_preserved_without_becoming_a_series_identity_gate(tmp_path: Path) -> None:
    """Provider taxonomy can vary while code, unit, series name and dates remain checked."""
    body = json.loads(FIXTURE.read_bytes())
    body["RESULTSET"][0]["CATEGORY"] = "Synthetic alternate provider classification"
    result = acquire(tmp_path, json.dumps(body).encode())
    assert result.status == "accepted_with_limitations"
    normalized = json.loads((tmp_path / "runs/fx/normalized.json").read_bytes())
    assert normalized["category"] == "Synthetic alternate provider classification"
