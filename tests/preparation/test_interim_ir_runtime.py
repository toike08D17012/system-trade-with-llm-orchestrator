"""Exercise real persistent gates, raw publication and one-shot provenance."""

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

import httpx
import pytest

from stock_research_llm_orchestrator.contracts.detailed_analysis.v1.task import DetailedAnalysisTaskV1
from stock_research_llm_orchestrator.preparation.interim_ir_runtime import CAMPAIGN, acquire_runtime, verify_runtime
from stock_research_llm_orchestrator.preparation.storage import read_bundle
from stock_research_llm_orchestrator.requests.storage import DATABASE_FILENAME, ProductionRequestRepository
from stock_research_llm_orchestrator.sources.toyota_ir import production_policy


ROOT = Path(__file__).parents[2]


class Scenario:
    """Synthetic fixed PDF hashes with the real eight-target production lifecycle."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Keep campaign and published evidence private and entirely offline."""
        self.root = tmp_path
        (tmp_path / "runtime").mkdir(mode=0o700)
        self.now = datetime(2026, 9, 28, 12, tzinfo=UTC)
        self.task_bytes = (
            ROOT / "tests/fixtures/contracts/detailed-analysis/v1/detailed-analysis-task/valid/human-selected.json"
        ).read_bytes()
        self.task = DetailedAnalysisTaskV1.model_validate_json(self.task_bytes)
        policy = json.loads((ROOT / "config/financial-mapping/7203-interim-ir-approved.json").read_bytes())
        self.bodies = {d["url"]: ("%PDF-synthetic-" + d["key"]).encode() for d in policy["documents"]}
        for d in policy["documents"]:
            d["sha256"] = sha256(self.bodies[d["url"]]).hexdigest()
        self.policy = json.dumps(policy).encode()
        monkeypatch.setattr(production_policy, "POLICY_SHA256", sha256(self.policy).hexdigest())
        self.sent: list[datetime] = []
        self.status = 200
        self.changed = False
        self.expire = False

    def clock(self) -> datetime:
        """Return injectable UTC time shared by HTTP and durable gates."""
        return self.now

    def sleep(self, seconds: float) -> None:
        """Advance time without a real delay, never skip persistent gating."""
        self.now += timedelta(seconds=seconds)

    def handler(self, request: httpx.Request) -> httpx.Response:
        """Optionally fail or lose the lease while a send is in flight."""
        self.sent.append(self.now)
        if self.expire:
            self.now += timedelta(seconds=121)
        return httpx.Response(
            self.status,
            content=b"changed" if self.changed else self.bodies[str(request.url)],
            headers={"Content-Type": "application/pdf", "Retry-After": "60"},
        )

    def acquire(self) -> Path:
        """Execute the real Coordinator, private publication and DB verification."""
        output = self.root / "raw/accepted"
        acquire_runtime(
            config=ROOT / "config",
            runtime=self.root / "runtime",
            raw_root=self.root / "raw",
            output=output,
            task_bytes=self.task_bytes,
            policy_bytes=self.policy,
            clock=self.clock,
            sleep=self.sleep,
            transport=httpx.MockTransport(self.handler),
        )
        return output

    def verify(self, output: Path) -> None:
        """Read canonical DB records rather than trusting exported receipts."""
        verify_runtime(
            read_bundle(output),
            ProductionRequestRepository(self.root / "runtime" / DATABASE_FILENAME),
            self.root / "raw",
            self.task,
            self.root / "runtime",
        )


def test_eight_acquisitions_and_db_replay(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Historical publications remain valid after later lease generations."""
    s = Scenario(tmp_path, monkeypatch)
    output = s.acquire()
    s.verify(output)
    assert len(s.sent) == 8
    assert all((b - a).total_seconds() >= 60 for a, b in zip(s.sent, s.sent[1:], strict=False))
    assert json.loads((output / "manifest.json").read_bytes())["analysis_ready"] is False
    with pytest.raises(ValueError, match="output_invalid"):
        s.acquire()
    with pytest.raises(FileExistsError):
        acquire_runtime(
            config=ROOT / "config",
            runtime=tmp_path / "runtime",
            raw_root=tmp_path / "raw",
            output=tmp_path / "raw/different",
            task_bytes=s.task_bytes,
            policy_bytes=s.policy,
            clock=s.clock,
            sleep=s.sleep,
            transport=httpx.MockTransport(s.handler),
        )
    assert len(s.sent) == 8
    data = json.loads((output / "manifest.json").read_bytes())
    ref = data["receipts"][0]["raw_reference"]
    raw = tmp_path / "raw" / ref["task_id"] / ref["relative_path"] / "body.bin"
    raw.write_bytes(raw.read_bytes() + b"tampered")
    with pytest.raises(ValueError, match="published_raw_mismatch"):
        s.verify(output)


@pytest.mark.parametrize("status", [302, 429, 500])
def test_http_failure_stops_without_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: int) -> None:
    """A failed first request consumes the whole campaign and prevents later sends."""
    s = Scenario(tmp_path, monkeypatch)
    s.status = status
    with pytest.raises((ValueError, RuntimeError)):
        s.acquire()
    assert len(s.sent) == 1 and not (tmp_path / "raw/accepted").exists()
    with pytest.raises(FileExistsError):
        s.acquire()
    assert len(s.sent) == 1


@pytest.mark.parametrize("mode", ["changed", "expire", "marker"])
def test_content_lease_and_crash_stop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    """No success bundle after changed content, in-flight expiry or marker-only crash."""
    s = Scenario(tmp_path, monkeypatch)
    if mode == "marker":
        from stock_research_llm_orchestrator.requests.storage import initialize_runtime_storage

        initialize_runtime_storage(tmp_path / "runtime")
        (tmp_path / "runtime" / CAMPAIGN).mkdir()
    else:
        setattr(s, mode, True)
    with pytest.raises((ValueError, RuntimeError, OSError)):
        s.acquire()
    assert len(s.sent) == (0 if mode == "marker" else 1)
    assert not (tmp_path / "raw/accepted").exists()
    with pytest.raises((FileExistsError, RuntimeError)):
        s.acquire()


@pytest.mark.parametrize(
    "table, column, value",
    [
        ("logical_requests", "request_fingerprint", "0" * 64),
        ("physical_attempts", "logical_request_id", "forged-parent"),
        ("raw_publications", "state", "reconciliation_required"),
        ("logical_results", "outcome", "failed"),
    ],
)
def test_db_tampering_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, table: str, column: str, value: str
) -> None:
    """Receipt/body equality cannot override a mismatched canonical runtime record."""
    s = Scenario(tmp_path, monkeypatch)
    output = s.acquire()
    with sqlite3.connect(tmp_path / "runtime" / DATABASE_FILENAME) as connection:
        connection.execute(f"UPDATE {table} SET {column} = ? WHERE rowid = (SELECT MIN(rowid) FROM {table})", (value,))
    with pytest.raises((ValueError, RuntimeError)):
        s.verify(output)


def test_other_task_and_forged_receipt_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An acquisition cannot be attached to another task or fake publication."""
    s = Scenario(tmp_path, monkeypatch)
    output = s.acquire()
    files = read_bundle(output)
    repository = ProductionRequestRepository(tmp_path / "runtime" / DATABASE_FILENAME)
    with pytest.raises(ValueError, match="binding_invalid"):
        verify_runtime(
            files, repository, tmp_path / "raw", s.task.model_copy(update={"task_id": "other"}), tmp_path / "runtime"
        )
    manifest = json.loads(files["manifest.json"])
    manifest["receipts"][0]["raw_reference"]["publication_id"] = "forged"
    files["manifest.json"] = json.dumps(manifest).encode()
    with pytest.raises(ValueError, match="database_binding_invalid"):
        verify_runtime(files, repository, tmp_path / "raw", s.task, tmp_path / "runtime")


@pytest.mark.parametrize("stage", ["commit", "finalize"])
def test_publication_and_finalization_interruption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
) -> None:
    """A persisted raw response never permits success or replay after interruption."""
    from stock_research_llm_orchestrator.requests.storage import RuntimeStorageError
    from stock_research_llm_orchestrator.requests.transport import ProductionTransportCoordinator

    s = Scenario(tmp_path, monkeypatch)

    def interrupt(*args: object, **kwargs: object) -> None:
        raise RuntimeStorageError("injected_interruption")

    if stage == "commit":
        monkeypatch.setattr(ProductionRequestRepository, "commit_raw_publication", interrupt)
    else:
        monkeypatch.setattr(ProductionTransportCoordinator, "finalize_logical_request", interrupt)
    with pytest.raises(RuntimeError):
        s.acquire()
    assert len(s.sent) == 1 and not (tmp_path / "raw/accepted").exists()
    assert list((tmp_path / "raw").glob("*/acquisitions/*/*/body.bin"))
    with sqlite3.connect(tmp_path / "runtime" / DATABASE_FILENAME) as connection:
        state = connection.execute("SELECT state FROM raw_publications").fetchone()[0]
        assert state == ("reconciliation_required" if stage == "commit" else "committed")
    with pytest.raises((FileExistsError, RuntimeError)):
        s.acquire()
    assert len(s.sent) == 1


def test_unapproved_transport_target_never_sends(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An otherwise valid intent cannot substitute an arbitrary website or URL."""
    from stock_research_llm_orchestrator.sources.toyota_ir.httpx_transport import ToyotaIrTransport

    s = Scenario(tmp_path, monkeypatch)
    policy = production_policy.load_policy(s.policy)
    intent = production_policy.intent_for(policy, policy.documents[0].key)
    physical = ToyotaIrTransport(
        intent.model_copy(update={"source_id": "other"}),
        s.policy,
        s.clock,
        lambda received: None,
        httpx.MockTransport(s.handler),
    )
    with pytest.raises(ValueError, match="intent_invalid"):
        physical(intent.to_transport_request("logical", "physical"))
    assert s.sent == []


def test_foreign_queued_request_is_not_processed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Unexpected queued work stops the campaign without executing another task."""
    from stock_research_llm_orchestrator.preparation.interim_ir_runtime import LEASE
    from stock_research_llm_orchestrator.requests.production import (
        ProductionCachePolicy,
        ProductionLogicalRequest,
        QueuePolicy,
    )
    from stock_research_llm_orchestrator.requests.storage import initialize_runtime_storage

    s = Scenario(tmp_path, monkeypatch)
    repository = initialize_runtime_storage(tmp_path / "runtime")
    lease = repository.acquire_lease("foreign-owner", s.clock(), LEASE)
    request = ProductionLogicalRequest(
        logical_request_id="foreign",
        task_id="foreign-task",
        source_id="toyota-ir",
        operation="interim-pdf",
        request_fingerprint="0" * 64,
        source_approval_version=1,
        source_profile_version=1,
        credential_scope_alias=None,
        egress_scope="default-egress",
        created_at=s.clock().isoformat(),
    )
    repository.admit_logical_request(
        request, "toyota-public-ir", ProductionCachePolicy(applicable=False), lease, s.clock(), QueuePolicy()
    )
    repository.release_lease(lease, s.clock())
    with pytest.raises(ValueError, match="queue_not_claimed"):
        s.acquire()
    assert s.sent == []
    assert "dequeued" in {event for event, reason in repository.queue_events("foreign")}


def test_cli_requires_network_opt_in(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Acquisition without opt-in stops before reading inputs or creating runtime."""
    from stock_research_llm_orchestrator.preparation.interim_ir_runtime_cli import main

    with pytest.raises(SystemExit) as stopped:
        main(
            [
                "acquire",
                "--runtime",
                str(tmp_path / "runtime"),
                "--raw-root",
                str(tmp_path / "raw"),
                "--task",
                "unused",
                "--policy",
                "unused",
                "--output",
                str(tmp_path / "raw/accepted"),
            ]
        )
    assert stopped.value.code == 2
    assert "requires --allow-network" in capsys.readouterr().err
    assert not (tmp_path / "runtime").exists()


def test_v1_send_blocks_v2_other_task_and_operation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Both source versions share provider/origin/egress gates across task IDs."""
    s = Scenario(tmp_path, monkeypatch)
    s.acquire()
    calls: list[str] = []

    def candidate_handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, content=b"unexpected", headers={"Content-Type": "application/pdf"})

    task = s.task.model_copy(update={"task_id": "other-disclosure-task"})
    with pytest.raises((ValueError, RuntimeError)):
        acquire_runtime(
            config=ROOT / "config",
            runtime=tmp_path / "runtime",
            raw_root=tmp_path / "candidates",
            output=tmp_path / "candidates/accepted",
            task_bytes=task.model_dump_json().encode(),
            policy_bytes=(ROOT / "config/financial-mapping/7203-latest-disclosure-candidates.json").read_bytes(),
            latest_disclosures=True,
            clock=s.clock,
            sleep=s.sleep,
            transport=httpx.MockTransport(candidate_handler),
        )
    assert calls == []
    repository = ProductionRequestRepository(tmp_path / "runtime" / DATABASE_FILENAME)
    with sqlite3.connect(tmp_path / "runtime" / DATABASE_FILENAME) as connection:
        identity = connection.execute(
            "SELECT logical_request_id FROM logical_requests WHERE operation = 'disclosure-pdf'"
        ).fetchone()
    assert identity is not None
    assert ("blocked", "global", "minimum_interval") in repository.gate_events(identity[0])
    assert repository.logical_result(identity[0]) is None
